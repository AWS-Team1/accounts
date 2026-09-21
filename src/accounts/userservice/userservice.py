# CI pipeline test@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@

# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Userservice manages user account creation, user login, and related tasks
"""

import atexit
from datetime import datetime, timedelta
import logging
import os
import sys
import re
import uuid

import bcrypt
import jwt
from flask import Flask, jsonify, request
import bleach
import redis
from redis.cluster import RedisCluster
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from opentelemetry import trace
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.propagate import set_global_textmap
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
from opentelemetry.instrumentation.flask import FlaskInstrumentor

from db import UserDb

def create_app():
    """Flask application factory to create instances
    of the Userservice Flask App
    """
    app = Flask(__name__)

    redis_client = None
    redis_url = os.environ.get('REDIS_URL')
    if redis_url:
        redis_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    elif os.environ.get('VALKEY_HOST'):
        use_tls = os.environ.get('VALKEY_TLS', 'true').lower() == 'true'
        client_class = (RedisCluster if os.environ.get(
            'VALKEY_CLUSTER_MODE', 'true').lower() == 'true' else redis.Redis)
        redis_client = client_class(
            host=os.environ['VALKEY_HOST'],
            port=int(os.environ.get('VALKEY_PORT', '6379')),
            username=os.environ.get('VALKEY_USERNAME'),
            password=os.environ.get('VALKEY_PASSWORD'),
            ssl=use_tls,
            ssl_cert_reqs='required' if use_tls else None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    # Disabling unused-variable for lines with route decorated functions
    # as pylint thinks they are unused
    # pylint: disable=unused-variable

    @app.route('/version', methods=['GET'])
    def version():
        """
        Service version endpoint
        """
        return app.config['VERSION'], 200

    @app.route('/ready', methods=['GET'])
    def readiness():
        """
        Readiness probe
        """
        return 'ok', 200

    @app.route('/users', methods=['POST'])
    def create_user():
        """Create a user record.

        Fails if that username already exists.

        Generates a unique accountid.

        request fields:
        - username
        - password
        - password-repeat
        - firstname
        - lastname
        - birthday
        - timezone
        - address
        - state
        - zip
        - ssn
        """
        signup_lock = None
        try:
            app.logger.debug('Sanitizing input.')
            req = {k: bleach.clean(v) for k, v in request.form.items()}
            __validate_new_user(req)

            # Serialize signup for a username across all userservice pods.
            # PostgreSQL remains the system of record; Redis only coordinates
            # concurrent requests and stores no user profile or password data.
            if redis_client is not None:
                signup_lock = redis_client.lock(
                    'signup:{}'.format(req['username'].lower()),
                    timeout=int(os.environ.get('SIGNUP_LOCK_TTL_SECONDS', '15')),
                    blocking_timeout=int(
                        os.environ.get('SIGNUP_LOCK_WAIT_SECONDS', '3')
                    ),
                )
                if not signup_lock.acquire():
                    return 'signup already in progress', 409

            # Check if user already exists
            if users_db.get_user(req['username']) is not None:
                raise NameError('user {} already exists'.format(req['username']))

            # Create password hash with salt
            app.logger.debug("Creating password hash.")
            password = req['password']
            salt = bcrypt.gensalt()
            passhash = bcrypt.hashpw(password.encode('utf-8'), salt)

            accountid = users_db.generate_accountid()

            # Create user data to be added to the database
            user_data = {
                'accountid': accountid,
                'username': req['username'],
                'passhash': passhash,
                'firstname': req['firstname'],
                'lastname': req['lastname'],
                'birthday': req['birthday'],
                'timezone': req['timezone'],
                'address': req['address'],
                'state': req['state'],
                'zip': req['zip'],
                'ssn': req['ssn'],
            }
            # Add user_data to database
            app.logger.debug("Adding user to the database")
            users_db.add_user(user_data)
            app.logger.info("Successfully created user.")

        except UserWarning as warn:
            app.logger.error("Error creating new user: %s", str(warn))
            return str(warn), 400
        except NameError as err:
            app.logger.error("Error creating new user: %s", str(err))
            return str(err), 409
        except SQLAlchemyError as err:
            app.logger.error("Error creating new user: %s", str(err))
            return 'failed to create user', 500
        except RedisError as err:
            app.logger.error("Redis unavailable during signup: %s", str(err))
            return 'signup temporarily unavailable', 503
        finally:
            if signup_lock is not None:
                try:
                    if signup_lock.owned():
                        signup_lock.release()
                except RedisError:
                    app.logger.warning("Failed to release signup lock", exc_info=True)

        return jsonify({}), 201

    def __validate_new_user(req):
        app.logger.debug('validating create user request: %s', str(req))
        # Check if required fields are filled
        fields = (
            'username',
            'password',
            'password-repeat',
            'firstname',
            'lastname',
            'birthday',
            'timezone',
            'address',
            'state',
            'zip',
            'ssn',
        )
        if any(f not in req for f in fields):
            raise UserWarning('missing required field(s)')
        if any(not bool(req[f] or req[f].strip()) for f in fields):
            raise UserWarning('missing value for input field(s)')

        # Verify username contains only 2-15 alphanumeric or underscore characters
        if not re.match(r"\A[a-zA-Z0-9_]{2,15}\Z", req['username']):
            raise UserWarning('username must contain 2-15 alphanumeric characters or underscores')
        # Check if passwords match
        if not req['password'] == req['password-repeat']:
            raise UserWarning('passwords do not match')

    def _issue_token(user, token_type, expiry_seconds):
        """Create a signed internal JWT for an authenticated user."""
        now = datetime.utcnow()
        full_name = user.get('name') or '{} {}'.format(
            user['firstname'], user['lastname'])
        payload = {
            'user': user['username'],
            'acct': user['accountid'],
            'name': full_name,
            'iat': now,
            'exp': now + timedelta(seconds=expiry_seconds),
            'jti': uuid.uuid4().hex,
            'token_type': token_type,
        }
        return jwt.encode(payload, app.config['PRIVATE_KEY'], algorithm='RS256')

    @app.route('/login', methods=['POST'])
    def login():
        """Login a user and return server-side access and refresh JWTs.

        Fails if username doesn't exist or password doesn't match hash

        token expiry time determined by environment variable

        request fields:
        - username
        - password
        """
        app.logger.debug('Sanitizing login input.')
        username = bleach.clean(request.form.get('username', ''))
        password = request.form.get('password', '')
        if not username or not password:
            return 'missing username or password', 400

        # Get user data
        try:
            app.logger.debug('Getting the user data.')
            user = users_db.get_user(username)
            if user is None:
                raise LookupError('user {} does not exist'.format(username))

            # Validate the password
            app.logger.debug('Validating the password.')
            if not bcrypt.checkpw(password.encode('utf-8'), user['passhash']):
                raise PermissionError('invalid login')

            app.logger.debug('Creating access and refresh tokens.')
            access_token = _issue_token(
                user, 'access', app.config['ACCESS_TOKEN_EXPIRY_SECONDS'])
            refresh_token = _issue_token(
                user, 'refresh', app.config['REFRESH_TOKEN_EXPIRY_SECONDS'])
            app.logger.info('Login Successful.')
            return jsonify({
                'access_token': access_token,
                'refresh_token': refresh_token,
            }), 200

        except LookupError as err:
            app.logger.error('Error logging in: %s', str(err))
            return str(err), 404
        except PermissionError as err:
            app.logger.error('Error logging in: %s', str(err))
            return str(err), 401
        except SQLAlchemyError as err:
            app.logger.error('Error logging in: %s', str(err))
            return 'failed to retrieve user information', 500

    @app.route('/token/refresh', methods=['POST'])
    def refresh_access_token():
        """Exchange a valid refresh JWT for a new short-lived access JWT."""
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer '):
            return 'missing refresh token', 401
        refresh_token = authorization.removeprefix('Bearer ').strip()
        try:
            claims = jwt.decode(
                jwt=refresh_token,
                key=app.config['PUBLIC_KEY'],
                algorithms=['RS256'],
            )
            if claims.get('token_type') != 'refresh':
                raise jwt.InvalidTokenError('wrong token type')
            user = {
                'username': claims['user'],
                'accountid': claims['acct'],
                'name': claims['name'],
            }
            access_token = _issue_token(
                user, 'access', app.config['ACCESS_TOKEN_EXPIRY_SECONDS'])
            return jsonify({'access_token': access_token}), 200
        except (jwt.InvalidTokenError, KeyError) as err:
            app.logger.info('Refresh token rejected: %s', str(err))
            return 'invalid refresh token', 401

    @atexit.register
    def _shutdown():
        """Executed when web app is terminated."""
        app.logger.info("Stopping userservice.")

    # Set up logger
    app.logger.handlers = logging.getLogger('gunicorn.error').handlers
    app.logger.setLevel(logging.getLogger('gunicorn.error').level)
    app.logger.info('Starting userservice.')

    # Set up tracing and export spans to Cloud Trace.
    if os.environ['ENABLE_TRACING'] == "true":
        app.logger.info("✅ Tracing enabled.")
        # Set up tracing and export spans to Cloud Trace
        trace.set_tracer_provider(TracerProvider())
        cloud_trace_exporter = CloudTraceSpanExporter()
        trace.get_tracer_provider().add_span_processor(
            BatchSpanProcessor(cloud_trace_exporter)
        )
        set_global_textmap(CloudTraceFormatPropagator())
        FlaskInstrumentor().instrument_app(app)
    else:
        app.logger.info("🚫 Tracing disabled.")

    app.config['VERSION'] = os.environ.get('VERSION')
    app.config['ACCESS_TOKEN_EXPIRY_SECONDS'] = int(
        os.environ.get('ACCESS_TOKEN_EXPIRY_SECONDS', '120'))
    app.config['REFRESH_TOKEN_EXPIRY_SECONDS'] = int(
        os.environ.get('REFRESH_TOKEN_EXPIRY_SECONDS', '3600'))
    app.config['PRIVATE_KEY'] = open(os.environ.get('PRIV_KEY_PATH'), 'r').read()
    app.config['PUBLIC_KEY'] = open(os.environ.get('PUB_KEY_PATH'), 'r').read()

    # Configure database connection
    try:
        users_db = UserDb(os.environ.get("ACCOUNTS_DB_URI"), app.logger)
    except OperationalError:
        app.logger.critical("users_db database connection failed")
        sys.exit(1)
    return app


if __name__ == "__main__":
    # Create an instance of flask server when called directly
    USERSERVICE = create_app()
    USERSERVICE.run()
