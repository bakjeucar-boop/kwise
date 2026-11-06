"""웹서버 없는 데스크톱 화면 (pywebview + Vue/Quasar).

창(WebView2)이 앱 안의 ``static\\index.html`` 을 **파일로 직접 연다** — 포트를 열지 않는다.
화면의 단추와 입력은 pywebview 의 다리(``js_api``)로 :class:`~kwise.webapp.api.Api` 의 메서드를
부르고, 그 메서드는 공용 계산 · 산출물 함수를 부른다. **공용 코드는 고치지 않는다.**

앞서 있던 NiceGUI 판(``kwise.desktop`` · 이 PC 안 서버에 붙는 방식)은 이 판이 같은 확인을 다
통과한 뒤 지웠다 (2026-11-06).
"""
