"""Web UI (Selenium / Playwright) Runner 组件：

    src/runners/web/
        adapters.py   — WebDriverAdapter 抽象 + Playwright / Selenium 两种实现
        session.py    — WebSession：一条 Case 的浏览器生命周期单元

和 `src/runners/app/` 是平行层：app 走 Appium，web 走 Selenium/Playwright，
通过统一的 StepRunner 协议挂到 dispatcher 上。
"""
