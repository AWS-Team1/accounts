# accounts

Bank of Anthos의 **계정 도메인** 소스 저장소입니다. `accounts-db`를 소유하는 서비스만 담습니다.

## 구성

| 경로 | 역할 |
| --- | --- |
| `src/accounts/userservice` | 사용자 등록·인증. **RS256 개인키로 JWT를 발급하는 유일한 서비스입니다** |
| `src/accounts/contacts` | 연락처(수취인 목록) 관리 |
| `src/accounts/accounts-db` | 계정 스키마와 테스트 데이터. 아래 “DB 디렉터리” 참조 |

Python 전용입니다. `pom.xml`·`mvnw`가 없습니다 — Java 서비스는 `ledger` 저장소에 있습니다.

## 왜 이 셋인가

`accounts-db` 라는 **하나의 데이터 소유권 경계**입니다. 이 DB를 읽고 쓰는 서비스는 userservice와
contacts 둘뿐입니다. 거래 원장(`ledger-db`)을 다루는 서비스는 `ledger` 저장소에 있고, 두 도메인은
DB를 공유하지 않습니다. 서비스 간 통신은 HTTP와 JWT로만 이루어집니다.

## 빌드 · 테스트

패키지 관리자는 **uv**입니다. pip이 아닙니다.

```sh
cd src/accounts/userservice && uv run pytest tests/ -v
cd src/accounts/contacts    && uv run pytest tests/ -v
```

이미지 빌드 컨텍스트는 각 서비스 디렉터리입니다.

```sh
docker build -t bank-userservice ./src/accounts/userservice
docker build -t bank-contacts    ./src/accounts/contacts
```

## 실행 환경

| 변수 | userservice | contacts | 용도 |
| --- | :---: | :---: | --- |
| `ACCOUNTS_DB_URI` 또는 `DB_HOST` `DB_PORT` `POSTGRES_USER` `POSTGRES_PASSWORD` `ACCOUNTS_DB_NAME` | ● | ● | DB 접속 |
| `PRIV_KEY_PATH` | ● | | JWT 서명용 **개인키**. 이 저장소에서 userservice만 사용합니다 |
| `PUB_KEY_PATH` | ● | ● | JWT 검증용 공개키 |
| `TOKEN_EXPIRY_SECONDS` | ● | | 토큰 수명 |
| `PORT` `LOG_LEVEL` `VERSION` | ● | ● | 동작 설정 |
| `REDIS_URL` | ● | | **선택.** 아래 참조 |

주입되는 값의 정본은 `gitops` 저장소의 `k8s/base/userservice/` 매니페스트입니다.
실제 인증정보는 이 저장소에 두지 않습니다.

### `REDIS_URL` 은 선택값입니다

`userservice.py` 는 `REDIS_URL` 이 설정된 경우에만 Redis 클라이언트를 만들고, 회원가입 시
`signup:<username>` 락으로 Pod 간 동시 요청을 직렬화합니다. 설정하지 않으면 클라이언트가
`None` 이 되어 Redis 없이 동작합니다. PostgreSQL이 진실의 원천이며 Redis에 자격증명을 저장하지
않습니다. 테스트는 `REDIS_URL` 을 설정하지 않으므로 Redis 없이, 인메모리 SQLite로 돕니다.

> 현재 `gitops` 저장소의 매니페스트에는 `REDIS_URL` 주입이 없습니다(`k8s/` 전체 검색 0건).
> 즉 지금 배포 형상에서는 Redis 경로가 꺼져 있습니다.

## DB 디렉터리 — AWS 배포 경로에서는 쓰이지 않습니다

`src/accounts/accounts-db/Dockerfile` 은 `postgres:16.14-alpine` 에 `initdb/` 를 얹어
**DB 컨테이너 이미지**를 만듭니다. 그러나 AWS 배포는 RDS를 사용하며, `gitops` 의 `k8s/base/` 에
DB 워크로드가 없고 배포 대상 이미지 목록에도 DB 이미지가 없습니다.

스키마는 `gitops` 의 `k8s/components/aws/db-bootstrap/` Job이 적용합니다. 그 Job이 쓰는
`sql/0-accounts-schema.sql` 은 이 저장소의 `accounts-db/initdb/0-accounts-schema.sql` 과
**내용이 같습니다.** 스키마를 바꿀 때는 **두 곳을 함께 고치거나**, 한쪽을 정본으로 정하고 다른
한쪽을 지워야 합니다. 한쪽만 고치면 조용히 어긋납니다.

## 저장소 범위

애플리케이션 코드와 이미지 빌드 정의만 포함합니다. Terraform, Kubernetes 매니페스트, 배포
스크립트는 포함하지 않습니다 — 각각 `infra`, `gitops` 저장소가 소유합니다.

소스 기준: `dlcodb94/aws-bank-of-anthos`의 `0ca63b4` 커밋. 라이선스는 `LICENSE`를 참고합니다.
