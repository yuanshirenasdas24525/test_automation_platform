/**
 * 加解密详细教程（右侧滑出抽屉，复用 SideDrawer）。
 *
 * 面向"在脚本库里写加解密脚本"的用户：讲清 RSA+AES-ECB 信封 + power-* 签名的
 * 数据流、crypto 工具箱 API、作用范围策略（全局/模块/用例/路径），以及脚本模板和常见坑。
 * 纯静态内容组件，不依赖后端。
 */
import type { ReactNode } from "react";

import { SideDrawer } from "@/components/ui/side-drawer";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <div className="space-y-2 text-sm leading-relaxed text-muted-foreground">{children}</div>
    </section>
  );
}

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
      <code>{children}</code>
    </pre>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.8em] text-foreground">{children}</code>
  );
}

/** 数据流示意图：请求发出前跑请求脚本，响应回来后跑响应脚本（两个钩子）。 */
function FlowDiagram() {
  return (
    <svg viewBox="0 0 720 200" className="h-auto w-full" role="img" aria-label="加解密数据流示意">
      {/* 客户端 */}
      <rect x="8" y="70" width="150" height="60" rx="8" className="fill-blue-100 stroke-blue-400 dark:fill-blue-950/60 dark:stroke-blue-600" strokeWidth="1.5" />
      <text x="83" y="95" textAnchor="middle" className="fill-blue-700 dark:fill-blue-300 text-[13px] font-semibold">平台（客户端）</text>
      <text x="83" y="114" textAnchor="middle" className="fill-blue-600/80 dark:fill-blue-400/80 text-[10px]">用例执行</text>

      {/* 被测系统 */}
      <rect x="562" y="70" width="150" height="60" rx="8" className="fill-emerald-100 stroke-emerald-400 dark:fill-emerald-950/60 dark:stroke-emerald-600" strokeWidth="1.5" />
      <text x="637" y="98" textAnchor="middle" className="fill-emerald-700 dark:fill-emerald-300 text-[13px] font-semibold">被测系统</text>
      <text x="637" y="115" textAnchor="middle" className="fill-emerald-600/80 dark:fill-emerald-400/80 text-[10px]">你的接口</text>

      {/* 请求箭头（上） */}
      <line x1="158" y1="82" x2="558" y2="82" className="stroke-slate-400 dark:stroke-slate-500" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <text x="358" y="66" textAnchor="middle" className="fill-foreground text-[11px] font-medium">① 请求脚本 crypto_request</text>
      <text x="358" y="79" textAnchor="middle" className="fill-muted-foreground text-[10px]">发出前：按你的算法加密 / 加签 headers、body</text>

      {/* 响应箭头（下） */}
      <line x1="558" y1="120" x2="158" y2="120" className="stroke-slate-400 dark:stroke-slate-500" strokeWidth="1.5" markerEnd="url(#arrow)" />
      <text x="358" y="140" textAnchor="middle" className="fill-foreground text-[11px] font-medium">② 响应脚本 crypto_response</text>
      <text x="358" y="153" textAnchor="middle" className="fill-muted-foreground text-[10px]">断言前：把响应解密回明文</text>

      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" className="fill-slate-400 dark:fill-slate-500" />
        </marker>
      </defs>
    </svg>
  );
}

/** 作用范围策略决策示意。 */
function ScopeDiagram() {
  return (
    <svg viewBox="0 0 720 150" className="h-auto w-full" role="img" aria-label="加解密作用范围判定">
      <rect x="8" y="55" width="150" height="42" rx="8" className="fill-slate-100 stroke-slate-300 dark:fill-slate-800 dark:stroke-slate-600" strokeWidth="1.5" />
      <text x="83" y="80" textAnchor="middle" className="fill-foreground text-[12px] font-semibold">一次请求</text>

      <rect x="230" y="20" width="200" height="42" rx="8" className="fill-amber-100 stroke-amber-400 dark:fill-amber-950/50 dark:stroke-amber-600" strokeWidth="1.5" />
      <text x="330" y="38" textAnchor="middle" className="fill-amber-700 dark:fill-amber-300 text-[11px] font-semibold">用例开关 rel_crypto?</text>
      <text x="330" y="52" textAnchor="middle" className="fill-amber-600/80 dark:fill-amber-400/80 text-[10px]">1/0 → 强制开/关（最高优先）</text>

      <rect x="230" y="88" width="200" height="42" rx="8" className="fill-violet-100 stroke-violet-400 dark:fill-violet-950/50 dark:stroke-violet-600" strokeWidth="1.5" />
      <text x="330" y="106" textAnchor="middle" className="fill-violet-700 dark:fill-violet-300 text-[11px] font-semibold">全局策略 crypto_scope</text>
      <text x="330" y="120" textAnchor="middle" className="fill-violet-600/80 dark:fill-violet-400/80 text-[10px]">all / include / exclude</text>

      <rect x="500" y="55" width="212" height="42" rx="8" className="fill-emerald-100 stroke-emerald-400 dark:fill-emerald-950/50 dark:stroke-emerald-600" strokeWidth="1.5" />
      <text x="606" y="73" textAnchor="middle" className="fill-emerald-700 dark:fill-emerald-300 text-[11px] font-semibold">should_apply → 加密 / 放行</text>
      <text x="606" y="87" textAnchor="middle" className="fill-emerald-600/80 dark:fill-emerald-400/80 text-[10px]">命中 modules/cases/paths?</text>

      <line x1="158" y1="72" x2="226" y2="41" className="stroke-slate-300 dark:stroke-slate-600" strokeWidth="1.5" markerEnd="url(#arrow2)" />
      <line x1="158" y1="80" x2="226" y2="109" className="stroke-slate-300 dark:stroke-slate-600" strokeWidth="1.5" markerEnd="url(#arrow2)" />
      <line x1="430" y1="41" x2="497" y2="70" className="stroke-slate-300 dark:stroke-slate-600" strokeWidth="1.5" markerEnd="url(#arrow2)" />
      <line x1="430" y1="109" x2="497" y2="82" className="stroke-slate-300 dark:stroke-slate-600" strokeWidth="1.5" markerEnd="url(#arrow2)" />
      <defs>
        <marker id="arrow2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" className="fill-slate-300 dark:fill-slate-600" />
        </marker>
      </defs>
    </svg>
  );
}

const REQUEST_SKELETON = `# 类型：请求加密 (crypto_request)
def handler(headers, body, config, vars=None):
    # headers: 请求头 dict | body: 请求体 | config: encryption_decryption 配置 | vars: 变量池
    # 在这里按你的算法改 headers / body
    return headers, body        # 必须返回 (新headers, 新body)`;

const RESPONSE_SKELETON = `# 类型：响应解密 (crypto_response)
def handler(response_body, config, vars=None):
    # response_body: 服务端返回体（已按 JSON 解析）
    # 在这里解密，返回明文 dict/list —— 后续断言/提取用它
    return response_body`;

const CUSTOM_EXAMPLE = `# 自己拼一套：整体 AES-GCM 加密 + HMAC 签名头
def handler(headers, body, config, vars=None):
    if not crypto.should_apply(config, vars):
        return headers, body
    key = config.get("key") or "my-secret"
    token = crypto.aes_gcm_encrypt(body, key)          # 密文字符串
    headers = dict(headers or {})
    headers["X-Sign"] = crypto.hmac_sha256(token, key) # 签名
    return headers, {"data": token}                    # 换成你服务端要的字段名`;

const REQUEST_SCRIPT = `def handler(headers, body, config, vars=None):
    # 作用范围判定：全局 / 指定用例 / 指定模块 / 指定路径
    if not crypto.should_apply(config, vars):
        return headers, body
    headers = dict(headers or {})
    # 可选：对明文业务参数加 power-* 签名头
    if str(config.get("sign_on") or "").strip().lower() in ("1", "true", "on", "yes", "y"):
        params = body if isinstance(body, dict) else {}
        ts = str(crypto.now_ms())
        nonce = crypto.random_hex(6)
        secret = config.get("sign_secret") or "rel-echo-sign-secret-2026"
        raw = crypto.canonical(params) + "&" + ts + nonce + secret
        headers["power-timestamp"] = ts
        headers["power-nonce"] = nonce
        headers["power-access-key"] = config.get("sign_access_key") or "REL_ECHO_AK"
        headers["power-sign"] = crypto.md5(raw)
    # RSA+AES-ECB 加密请求体
    return headers, crypto.rsa_aes_ecb_encrypt(
        body, public_key_pem=config.get("rsa_public_key") or crypto.TEST_PUBLIC_KEY_PEM
    )`;

const RESPONSE_SCRIPT = `def handler(response_body, config, vars=None):
    if not crypto.should_apply(config, vars):
        return response_body
    if isinstance(response_body, dict) and "key" in response_body and "data" in response_body:
        return crypto.rsa_aes_ecb_decrypt(response_body)
    return response_body`;

const CONFIG_BASE = `on_off = true
custom_request_handler = <你的请求脚本名>
custom_response_handler = <你的响应脚本名>
custom_crypto_only = true          # 只走你的脚本，跳过平台内置签名/AES
key = <你的密钥>                    # 脚本里 config.get("key") 取；也可加任意自定义参数`;

const CONFIG_EXAMPLE = `on_off = true
custom_request_handler = rel_request_crypto
custom_response_handler = rel_response_crypto
custom_crypto_only = true
rsa_public_key = -----BEGIN PUBLIC KEY-----\\n...    # 留空用内置测试公钥
sign_on = true
# 只加密指定接口（三选一或混用）：
crypto_scope = include
crypto_paths = ["/api/auth/echo_test"]     # 按路径
# crypto_modules = ["支付"]                # 按模块
# crypto_cases   = ["下单用例", 1024]      # 按用例名/id`;

export function CryptoTutorialDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      storageKey="crypto-tutorial-drawer"
      minWidth={560}
      defaultWidth={720}
      title={
        <span className="flex items-center gap-2">
          <span className="text-base font-semibold">搭建你的加解密</span>
          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[11px] font-medium text-blue-700 dark:bg-blue-950/60 dark:text-blue-300">
            脚本库教程
          </span>
        </span>
      }
    >
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5">
        <Section title="脚本库能帮你做什么">
          <p>
            如果你的被测接口需要<b>加密请求 / 解密响应 / 加签名</b>才能调通，就在<b>脚本库</b>里写两段
            Python 脚本：平台会在<b>发请求前</b>和<b>收到响应后</b>自动调用它们。<b>算法完全由你定</b>——
            平台只负责"什么时候调"，加解密怎么算是你脚本里的事。本教程教你怎么搭，末尾用本系统的
            RSA+AES-ECB 做一个完整示例。
          </p>
          <div className="rounded-lg border bg-card p-3">
            <FlowDiagram />
          </div>
        </Section>

        <Section title="第 1 步 · 新建两条脚本">
          <p>脚本库 →「新建脚本」，各建一条，都要定义 <Kbd>handler</Kbd>：</p>
          <Code>{REQUEST_SKELETON}</Code>
          <Code>{RESPONSE_SKELETON}</Code>
          <p>参数约定固定，返回值约定：请求脚本返回 <Kbd>(headers, body)</Kbd>，响应脚本返回<b>解密后的对象</b>。</p>
        </Section>

        <Section title="第 2 步 · 用 crypto 工具箱写你的算法">
          <p>
            脚本现在运行在独立 Python 进程，可以直接 import 当前脚本运行环境已经安装的
            <Kbd>cryptography</Kbd>、<Kbd>httpx</Kbd> 等包。下面的 <Kbd>crypto</Kbd>
            示例属于旧脚本兼容写法；新项目建议显式 import 依赖，让脚本与平台代码完全解耦：
          </p>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1.5 pr-3 font-medium">函数</th>
                  <th className="py-1.5 font-medium">用途</th>
                </tr>
              </thead>
              <tbody className="align-top">
                {[
                  ["crypto.rsa_aes_ecb_encrypt(data, public_key_pem=None)", "公钥加密 → {key,data} 信封"],
                  ["crypto.rsa_aes_ecb_decrypt(payload, private_key_pem=None)", "私钥解密信封 → dict/list"],
                  ["crypto.aes_gcm_encrypt / aes_gcm_decrypt(text, key)", "AES-256-GCM 通用对称"],
                  ["crypto.md5 / sha256 / hmac_sha256(text[, key])", "摘要 / 签名"],
                  ["crypto.canonical(params, fields=None)", "旧脚本兼容：参数有序拼 k=v&k=v"],
                  ["crypto.now_ms() / random_hex(n) / b64encode / b64decode", "时间戳 / 随机 / 编解码"],
                  ["crypto.should_apply(config, vars)", "按作用范围策略判断是否加解密"],
                  ["crypto.TEST_PUBLIC_KEY_PEM / TEST_PRIVATE_KEY_PEM", "自测靶子内置密钥（仅自测）"],
                ].map(([fn, desc]) => (
                  <tr key={fn} className="border-b border-border/50">
                    <td className="py-1.5 pr-3 font-mono text-[11px] text-foreground">{fn}</td>
                    <td className="py-1.5">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="font-medium text-foreground">例：自己拼一套（AES-GCM 整体加密 + HMAC 签名）</p>
          <Code>{CUSTOM_EXAMPLE}</Code>
          <p>换成你服务端要的信封字段名、算法、签名头即可——这就是"搭建自己的加解密"。</p>
        </Section>

        <Section title="第 3 步 · 配置指到你的脚本">
          <p>项目配置 →「encryption_decryption」，填脚本名 + 开关：</p>
          <Code>{CONFIG_BASE}</Code>
          <p>
            <Kbd>custom_crypto_only=true</Kbd> = 只走你的脚本。密钥等参数放 config，脚本里 <Kbd>config.get("key")</Kbd> 取，
            <b>不写死在脚本里</b>，方便不同环境换值。
          </p>
        </Section>

        <Section title="第 4 步 · 控制作用范围：只让部分接口加密">
          <div className="rounded-lg border bg-card p-3">
            <ScopeDiagram />
          </div>
          <p>
            <Kbd>encryption_decryption</Kbd> 是<b>项目级全局配置</b>，靠 <Kbd>crypto.should_apply(config, vars)</Kbd> 分流：
            <b>用例开关 rel_crypto（1/0）优先级最高</b>，其次全局 <Kbd>crypto_scope</Kbd>：
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li><Kbd>all</Kbd>（默认）：全项目加密。</li>
            <li><Kbd>include</Kbd>：只有命中 <Kbd>crypto_modules</Kbd> / <Kbd>crypto_cases</Kbd> / <Kbd>crypto_paths</Kbd> 名单的加密。</li>
            <li><Kbd>exclude</Kbd>：名单之外的加密。</li>
          </ul>
          <p>
            <Kbd>crypto_paths</Kbd> 支持精确（<Kbd>/api/auth/echo_test</Kbd>）或前缀通配（<Kbd>/api/pay/*</Kbd>）。
            用例编辑器里的<b>加解密三态开关</b>就是写 <Kbd>rel_crypto</Kbd>，可对单用例强制开/关。
          </p>
        </Section>

        <Section title="第 5 步 · 测试与生效">
          <ol className="list-decimal space-y-1 pl-5">
            <li>脚本页右上「<b>测试运行</b>」：填 <Kbd>测试输入</Kbd>（headers/body/config/vars），看输出对不对。</li>
            <li>脚本内容改完即时生效；每次调用都会启动隔离进程，不会把模块或全局变量残留到下一条用例。</li>
            <li>跑一条命中的用例：请求体照写<b>明文</b>，断言照写<b>解密后</b>结构，加解密对用例透明。</li>
          </ol>
        </Section>

        <Section title="完整示例 · 本系统 RSA + AES-ECB + 签名">
          <p>把前面几步串起来的一个真实例子：RSA 公钥加密随机 AES 密钥、AES-ECB 加密请求体、叠加 power-* 签名头。可直接复制改成你的。</p>
          <p className="font-medium text-foreground">请求脚本（crypto_request）</p>
          <Code>{REQUEST_SCRIPT}</Code>
          <p className="font-medium text-foreground">响应脚本（crypto_response）</p>
          <Code>{RESPONSE_SCRIPT}</Code>
          <p className="font-medium text-foreground">配置</p>
          <Code>{CONFIG_EXAMPLE}</Code>
        </Section>

        <Section title="常见坑">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>允许 <Kbd>import</Kbd> 已安装依赖；未安装的包会明确报 <Kbd>ModuleNotFoundError</Kbd>，不需要向平台业务目录新增 Python 文件。</li>
            <li><Kbd>handler</Kbd> 可以调用脚本中同级定义的函数，也支持同步函数与 <Kbd>async def</Kbd>。</li>
            <li>脚本通过 JSON 与平台交换数据，不能直接 import <Kbd>server</Kbd>、<Kbd>database</Kbd>、<Kbd>runners</Kbd>，也拿不到平台数据库/JWT环境变量。</li>
            <li>报 <Kbd>handler 不存在</Kbd>：脚本没保存，或名字/类型和配置里的 handler 名不一致。</li>
          </ul>
        </Section>
      </div>
    </SideDrawer>
  );
}
