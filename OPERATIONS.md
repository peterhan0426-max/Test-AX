# 개인용 자동 갱신 운영

현재 운영 상태는 **배포 준비 중**입니다. 이 파일이나 `render.yaml`의 존재는
실제 호스팅 생성, 34개월 자료 이관, 다음 날 08:00 정시 실행이나 외부 백업 완료를 의미하지 않습니다.

## 실행 구조

한 Docker 서비스에서 조회 서버, 수집 작업, 백업 작업을 함께 실행합니다.
한 작업이 비정상 종료되면 전체 프로세스가 실패 상태로 종료되며 호스팅의 재시작 정책으로 복구합니다.

- 매일 08:00 한국 시간: 전월·당월 자료를 다시 조회하고 검증된 자료를 반영합니다.
- 웹 화면: 5분마다 최신 자료를 조회합니다. 과거 월 선택은 유지하고 새 월도 선택 목록에 추가합니다.
- 매일 09:00 한국 시간: SQLite의 일관된 사본과 해시별 원본을 백업하고 최근 7개를 보관합니다.
- 영구 디스크: `/var/saenong/data`에 원본과 분석 자료, `/var/saenong/backups`에 백업을 보관합니다.
- 접속: HTTPS를 제공하는 호스팅 뒤에서 사용합니다. 아이디는 `saenong`, 암호는 비공개 설정값입니다.

**같은 디스크의 백업만으로 디스크 손실에 대비할 수 없습니다.** 운영 계정에 연결된
별도 백업 목적지나 호스팅의 외부 스냅샷 정책을 구성하고 복구를 확인해야 합니다.
ATPOS 세션과 계정 비밀값은 백업에 넣지 않습니다.

## 호스팅 설정

`render.yaml`은 Render의 Docker 웹 서비스와 10GB 영구 디스크를 위한 설정입니다.
유료 서버·디스크 비용이 발생합니다. 이 세션에는 Render API 인증이나 다른 운영 서버 접속 권한이
연결돼 있지 않으므로, 호스팅 생성·비용 승인·실제 URL 확인은 아직 완료하지 않았습니다.
설정 형식은 Render 측 검증도 거쳐야 합니다.

- 비공개 설정: `ATPOS_ID`, `ATPOS_PASSWORD`.
- `SAENONG_VIEWER_PASSWORD`는 호스팅에서 자동 생성하며, 사용자에게 안전하게 전달합니다.
- HTTPS와 지속 실행, 비정상 종료 시 재시작, 영구 디스크를 활성화합니다.
- 원래 Codex에 연결한 인증 값은 해당 환경의 프록시 연결입니다. 새 호스팅에 원시 자격 증명이
  이미 전달됐다고 가정하거나 프록시 값을 추출하지 않습니다. 새 호스팅의 보안 입력 경로를 사용합니다.

Docker만 있는 서버에서도 같은 이미지를 실행할 수 있습니다.
비공개 환경 파일은 코드 저장소 밖에 보관하고 내용이나 `docker inspect`의 환경값을 공개하지 않습니다.

```sh
docker build -t saenong-runtime .
docker volume create saenong-private
docker run -d --name saenong --restart unless-stopped \
  --env-file /etc/saenong/private.env \
  -v saenong-private:/var/saenong \
  -p 127.0.0.1:8000:8000 saenong-runtime
```

이 명령은 HTTPS 프록시 뒤에서 운영하기 위한 내부 서버입니다. 사용자에게 loopback 링크를 전달하지 않습니다.
`SAENONG_AUTOCOLLECT=0`은 원본 사이트에 접속하지 않는 내부 검증용이며 실제 운영에서는 사용하지 않습니다.

## 데이터 이관과 복구

현재 34개월 자료는 저장소 밖에 보관하고 HTTPS 또는 SSH의 비공개 전송 경로로 이관합니다.
기존 자료를 덮어쓰지 않으며, 서비스 시작 전에 빈 자료 폴더에서 복구하고 원본 해시와 금액을 대조합니다.

```sh
python -m saenong.backup --data-dir /private/saenong --backup-dir /private/saenong-backups
python -m saenong.backup --data-dir /private/restored-saenong --restore /private/saenong-backups/BACKUP.tar.gz
```

백업 파일에는 실제 매출과 고객별 원본이 포함될 수 있으므로 비공개 저장·전송 경로만 사용합니다.
자동 백업 복원 시 세션은 새 호스팅에서 다시 인증합니다.

## 완료 판정

실제 개인용 HTTPS 주소에서 로그인·화면·기준일 표시를 확인하고, 34개월을 이관해 금액을 대조합니다.
호스팅 재시작 후 자료 유지와 작업 재실행, 정시 수집 후 화면 반영, 서버 밖 백업의 복구까지 확인해야
자동 운영 완료라고 보고합니다. 로컬 테스트나 설정 파일 생성으로 이를 대체하지 않습니다.
