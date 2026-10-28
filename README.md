# Webhook_MabinogiMobileNews

마비노기 모바일 공식 **공지·업데이트·이벤트·에린 노트**의 새 글을 감지하고 몰리봇 서버에 HTTPS 웹훅으로 전달합니다. Discord 웹훅에 직접 메시지를 쓰는 프로그램이 아닙니다.

Python 3.12+ 표준 라이브러리만 사용합니다. 별도 패키지나 Chromium 설치 없이 GitHub Actions에서 실행됩니다. 몰리봇 저장소/수신 서버는 이 저장소에 포함되지 않습니다.

## 동작

- 공지 `/News/Notice`, 업데이트 `/News/Update`, 이벤트 `/News/Events`, 에린 노트 `/News/Devnote` 전체 목록을 확인합니다.
- `data-threadid`와 제목·썸네일 URL을 추출합니다. PC·모바일 중복 마크업은 ID로 합칩니다.
- **최초 초기화는 전체 과거 목록을 순회해 기준 상태만 저장하며 알리지 않습니다.** 처음부터 첫 페이지만 기록하면 나중에 목록에 밀려 올라온 옛 글을 새 글로 오인할 수 있기 때문입니다.
- 이후에는 각 게시판을 첫 페이지부터 읽고, 모두 이미 확인한 글인 페이지 또는 마지막 페이지까지 탐색합니다. 게시글 ID의 크기를 시간으로 가정하지 않고 알려진 ID 집합과 비교합니다.
- 여러 새 글은 게시판별 ID 오름차순으로 전송합니다. 게시판 간 엄밀한 게시 시각 순서는 보장하지 않습니다.
- 새 글 ID와 전송 대기 목록(outbox)을 파일에 함께 저장한 뒤 전송합니다. 성공한 건만 대기 목록에서 제거합니다.
- 실패 시 다음 실행에서 같은 `eventId`로 다시 보냅니다. 수집 중 어느 게시판이라도 실패하면 이번 수집 결과는 저장하지 않습니다.
- 기존 글의 제목·본문 수정, 삭제, 이벤트 상태 변경은 알리지 않습니다. 기간 해석은 하지 않으므로 결과 발표나 기간 없는 글도 수집됩니다.
- HTML 구조 이상, 차단 페이지, 반복 페이지, 수집 도중 총 글 수 변경, 100페이지 상한 초과는 실패 처리합니다. 상태를 임의로 초기화하지 않습니다.

## 기존 세 게시판 상태에서 업그레이드

기존 상태 파일은 재초기화하지 않습니다. 공지·업데이트·이벤트 세 키만 있는 기존 상태를 읽으면, 다음 정상 실행에서 에린 노트의 전체 과거 글을 기준 상태에 추가하고 **에린 노트의 과거 글은 발송하지 않습니다**. 이후 새로 발견한 에린 노트부터 `board: "devnote"`로 전달합니다. 기존 게시판의 이력·전송 대기 목록은 유지합니다. 추가 게시판 초기화가 실패하면 이번 수집 상태를 저장하지 않고 다음 실행에서 다시 시도합니다.

몰리 수신부는 감지 활성화 전에 `devnote` 게시판 값을 허용해야 합니다. 에린 노트도 동일한 `schemaVersion: 1`, 제목·링크·선택 썸네일 형식이며 `category`로 `개발자노트`·`CM노트`를 전달합니다.

## GitHub Actions 설정 순서

정기 실행은 기본적으로 꺼져 있습니다. 아래 순서로 준비하세요.

1. 몰리 서버에 아래 규격의 HTTPS 수신부를 구현합니다.
2. 저장소 Settings → Secrets and variables → Actions → Secrets에 등록합니다.
   - `MOLLY_WEBHOOK_URL`: 예: `https://YOUR_DOMAIN/webhooks/mabinogi/news`
   - `MOLLY_WEBHOOK_TOKEN`: 몰리 서버와 공유하는 충분히 긴 전용 인증키
3. Actions → **Watch Mabinogi Mobile news** → Run workflow에서 `main`을 선택하고 `initialize`를 체크해 한 번 실행합니다. 전체 과거 글을 조회하므로 몇 분 걸릴 수 있습니다. 인증키나 수신부 없이 초기화만 먼저 할 수도 있습니다.
4. `state/news.json`이 커밋되고 실행이 성공했는지 확인합니다.
5. Actions Variables에 `NEWS_WATCHER_ENABLED` = `true`를 등록합니다.
6. `initialize`를 체크하지 않고 수동 실행해 수신 연동을 확인합니다.

초기화 이후 활성화 전까지 등록된 글은 활성화 시 새 글로 전달됩니다. 원하지 않는 공백을 피하려면 수신부 준비 후 초기화하세요.

예약 실행은 매시 7·17·27·37·47·57분(UTC)으로 약 10분 주기입니다. GitHub Actions 예약은 지연되거나 실행되지 않을 수 있어 실시간 알림을 보장하지 않습니다. 공개 저장소의 예약 비활성화 정책과 실행 사용량도 확인하세요.

상태 커밋에는 `contents: write`가 필요합니다. 기본 브랜치 보호가 봇의 직접 커밋을 막는다면 상태 저장 위치/권한 구성을 변경해야 합니다. 설정 키나 수신 URL을 상태 파일에 넣지 않습니다.

중지하려면 `NEWS_WATCHER_ENABLED`를 `false`로 바꾸거나 워크플로를 비활성화합니다. 다른 브랜치에서는 감지 워크플로를 실행하지 않습니다. 실행은 직렬화하며 진행 중 작업을 새 예약 작업이 취소하지 않습니다.

## 몰리 수신 계약 (schemaVersion 1)

```http
POST /webhooks/mabinogi/news
Authorization: Bearer <MOLLY_WEBHOOK_TOKEN>
Content-Type: application/json; charset=utf-8
Idempotency-Key: mabinogi-mobile:notice:3571370:created
```

```json
{
  "schemaVersion": 1,
  "eventId": "mabinogi-mobile:notice:3571370:created",
  "board": "notice",
  "category": "점검",
  "postId": "3571370",
  "title": "10/29(목) 정기점검 안내",
  "url": "https://mabinogimobile.nexon.com/News/Notice/3571370",
  "detectedAt": "2026-10-28T03:00:00+00:00",
  "thumbnailUrl": "https://dszw1qtcnsa5e.cloudfront.net/community/example/banner.png"
}
```

- `board`: `notice`, `update`, `events`, `devnote`. 시각은 UTC ISO 8601이며 몰리가 표시 시 KST로 변환합니다.
- `category`: 공지·에린 노트 목록에 표시된 분류 문구(예: `주요상품`, `점검`, `안내`, `완료`, `개발자노트`, `CM노트`)입니다. 고정된 열거형으로 제한하지 않고 원문을 공백 정규화해 전달합니다. 공지·에린 노트에 분류가 없거나 업데이트·이벤트 게시판이면 `null`입니다. 선택 필드이므로 기존 대기 이벤트에는 없을 수 있습니다. 기존 글의 분류가 바뀌어도 별도 알림을 보내지 않습니다.
- `thumbnailUrl`: 선택 필드인 이미지 URL입니다. 공지·업데이트는 목록 및 상단 고정 배너의 `.thumbnail` 배경 이미지, 이벤트는 `.thumbnail` 내부 이미지에서 추출합니다. 고정 배너는 게시글 링크의 ID로 실제 목록과 연결하며 배너 자체를 새 글로 추가하지 않습니다. 모든 글에 전용 썸네일이 있는 것은 아닙니다. 작성자 프로필은 제외합니다. 이미지가 없거나 주소 형식이 잘못되면 `null`로 전달하며, 알림은 정상 처리합니다. 이미지 다운로드/실제 표시 가능성은 검사하지 않습니다.
- 몰리는 `thumbnailUrl`이 있으면 Embed의 큰 이미지로 표시하고, 없거나 `null`이면 이미지 없이 표시하면 됩니다. 현재 이미지 주소는 감지기가 전달하며 Embed 구성은 몰리에서 구현할 사항입니다. 기존 대기 이벤트에는 필드가 없을 수 있어 누락도 허용해야 합니다. 선택 필드 추가이므로 `schemaVersion`과 `eventId` 규칙, 기존 상태 파일을 그대로 유지합니다. 수신부가 알 수 없는 필드를 거부한다면 감지 활성화 전에 수신부를 먼저 갱신하세요.
- `eventId`는 게시판·글 ID로 고정되며 재전송 시 유지됩니다.
- 몰리는 인증·스키마·공식 URL을 검증하고 **영구 저장 후에만 2xx**를 반환해야 합니다. 이미 저장한 `eventId`에도 2xx를 반환합니다.
- 2xx는 모든 디스코드 서버에 발송했다는 뜻이 아니라 **몰리가 전달 책임을 인수했다는 뜻**입니다. 서버별 발송·구독·재시도는 몰리의 책임입니다.
- 그 외 상태 코드, 타임아웃, 연결 실패는 대기 목록을 유지하고 실행을 실패시킵니다. 즉시 POST 재시도를 반복하지 않고 다음 예약 실행에서 재시도합니다. 3xx 리다이렉트는 따라가지 않습니다.
- 이 구성은 **최소 한 번 전달(at-least-once)**입니다. 응답 유실, 프로세스 종료, 상태 커밋 실패로 재전송될 수 있어 몰리의 `eventId` 중복 제거가 필수입니다.

## 로컬 검증

```bash
python -m unittest discover -s tests -v
python watcher.py --check
```

`--check`는 네 게시판 첫 페이지만 수집·해석하며 파일 저장과 웹훅 호출을 하지 않습니다.

```bash
python watcher.py --state state/news.json --initialize
# MOLLY_WEBHOOK_URL, MOLLY_WEBHOOK_TOKEN 환경변수 설정 후:
python watcher.py --state state/news.json
```

`--initialize`는 기존 상태 파일이 있으면 거부합니다. 상태가 없거나 손상되면 일반 실행은 실패합니다. 임의 삭제 대신 Git 이력에서 마지막 정상 상태를 복구하세요. 초기화는 알림 이력을 버리는 명시적 작업입니다.

테스트에는 공식 HTML에서 추출한 카드/페이지 정보 샘플과 다중 페이지, 중복 마크업, 수집 실패, 재시작 후 재전송, 인증 URL 검증 사례가 포함됩니다. 운영 환경에서 HTTP 접근이 차단되면 실패 로그를 확인해야 하며, 이 버전은 Playwright 자동 대체 경로를 포함하지 않습니다.
