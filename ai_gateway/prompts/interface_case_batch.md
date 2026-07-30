你是一名资深接口测试工程师。请基于下面的「接口摘要」，为「{{MODULE_NAME}}」模块**本批指定的测试点**生成详细的**接口测试用例**。

# 接口摘要（digest）
{{DIGEST}}

# ⚠️ 本项目真实响应结构与 API 约定（写 extract/assertion 的唯一依据）
{{PROJECT_CONTEXT}}

> **极重要**：下文示例里出现的 `$.data.token`、`$.code` 等只是"格式演示",**不是**本项目的真实结构。写 `extract` / `assertion` 的 JSONPath 时，**必须以上面这段项目真实约定为准**：响应用什么信封（如 `{status, data:{...}}`）、token 在哪个路径（如 `$.data.access_token`）、错误码/文案字段叫什么、是中文还是英文——全部照真实约定写。**上面若明确了结构，就绝不许再凭常识猜路径**。若上面为空/未覆盖某接口，才按最常见约定推断，并把不确定处标进 note。

# 当前项目其它模块（用于交叉考量）
{{CROSS_MODULE_CONTEXT}}

# 本批要生成的测试点（只为这些点生成，不要自行扩展别的点）
{{BATCH_POINTS}}

# 已经生成过 / 已存在的用例名（务必不要与这些重复）
{{DONE_NAMES}}

# 本模块现有用例（按当前执行顺序编号，用于决定每条新用例插在哪里）
{{EXISTING_ORDERED}}

# 可用变量池（这些变量已在配置中心 default_parameters 里有值，执行时 ${变量名} 会被替换；为空则无）
{{VARIABLE_POOL}}

# 可用动态函数（动态值只能用下面这些 `function:xxx`，**严禁自己造不存在的函数名**，如 `function:random_username` 不存在、会执行失败）
{{AVAILABLE_FUNCTIONS}}

# 硬性要求

1. **只覆盖上面列出的本批测试点**，一个测试点通常对应 1 条用例。
1.1. **用例名（name）要简短**，只写测试意图，**不要加方法和路径前缀**（写「创建订单成功并入库」而不是「POST /api/orders 创建订单成功并入库」）。方法和路径放进 steps 里。
2. **结构化接口字段（必须输出，这些才是可执行用例的核心）**：
   - `method`：请求方法，如 "POST"。
   - `path`：请求路径，如 "/api/orders"；可引用变量，如 "/api/orders/${orderId}"。
   - `headers`：请求头对象，如 {"Content-Type": "application/json", "Authorization": "Bearer ${token}"}；无则 {}。
   - `body`：请求体对象（JSON），如 {"productId": 1001, "qty": 2}；异常用例给触发异常的值（缺字段/错类型/超长）；无 body 用 {}。
2.0. **测试数据：优先用变量池，写入类数据用动态函数，其余写具体值（重要）**：
   - 如果某个测试数据在**上面的「可用变量池」里有对应变量**，就**直接用 `${变量名}`**（如登录用 `{"username": "${my_account}", "password": "${my_password}"}`、手机号用 `${mobile}`）——执行时会替换成真实值。
   - **正向「创建/注册/写入」类用例的新建数据，必须用动态唯一函数生成，禁止写死字面量**（避免重跑撞唯一键、避免污染固定业务数据）：用户名/账号用 `function:unique(AUTO_TEST_user)`、手机号用 `function:unique_mobile()`、邮箱用 `function:unique_email()`、其它需唯一的名称/单号用 `function:unique(AUTO_TEST_xxx)`。**函数参数不要加引号**（写 `function:unique(AUTO_TEST_user)`，不要写 `function:unique("AUTO_TEST_user")`——JSON 里嵌套引号容易写崩）。这些会在执行时生成带 `AUTO_TEST_` 命名空间的唯一值，便于事后清理。
   - **密码字段一律写固定字面量，绝不用 `function:unique`**（如 `"password": "Test@123"`、`"new_password": "NewTest@123"`）。原因：① 后续步骤/用例要用这个密码登录，必须是已知固定值，随机了就登不进去；② 密码通常有复杂度要求。`function:unique` 只用于用户名/手机号/邮箱/单号这类"需要唯一、又不用回头登录"的字段。
   - **逆向/参数校验/边界/安全用例例外**：这类要的就是畸形/非法/边界输入（缺字段、错类型、超长、注入串），**照常写触发异常的具体值**，不要套 `function:unique`。
   - **控制字符/不可见字符类安全用例：用可见转义串，禁止塞原始 NUL（U+0000 空字符）**。PostgreSQL 存不下真实的 NUL 空字符，会导致用例存不进库。要测控制字符时，写成可见的字面量字符串（例如带反斜杠的文本 `"test\\u0000user"`、`"a\\tb"`），不要在 JSON 里写会被解析成真实 NUL 的 unicode 转义。
   - 变量池里没有、又不是新建写入数据的字段（如 productId、qty、枚举值等），**写具体值**（如 `{"productId": 1001}`）。
   - **绝不能凭空造变量池里没有、也没有任何前置用例 `extract` 产出的变量**（如随手写个 `${foo}`、没有登录用例却写 `${token}`、没有注册用例却写 `${userId}`）——`${变量}` 执行时从变量池/提取结果取值，找不到来源就解析不了、请求发错。
   - **缺信息时填引导词，不要瞎编**：如果某个必需的值（如某个 token、账号、资源 id）既不在变量池、也无法由本批前置用例产出，**不要硬塞一个看似合理的假值**；把该字段值写成引导占位串 `"<TODO: 需补充xxx来源，如先调登录/创建接口提取>"`，提醒用户或下一轮 AI 补齐。
   - **动态函数只能用上面「可用动态函数」列出的那些**（如 `function:unique(前缀)`、`function:unique_mobile()`、`function:unique_email()`、`function:get_timestamp()` 等）。**严禁自己编函数名**——`function:random_username`、`function:random_string` 这类不存在，执行时会直接报错。需要唯一用户名就用 `function:unique(AUTO_TEST_user)`。
   - 运行时才有的值（token、新建数据的 id）走 `extract` 提取，再用 `${token}`/`${orderId}` 引用。
3. **提取参数 `extract`**（对象 {变量名: JSONPath}）：本接口响应里、后续用例要用到的值写在这里。JSONPath 用 `$.` 开头，如 `{"token": "$.data.token"}`、`{"orderId": "$.data.orderId"}`；无则 {}。登录类用例**务必**提取 token。
4. **断言 `assertion`**（对象 {JSONPath: 期望值}）：**每条用例都必须有断言，不允许空**。至少断言响应状态/业务码（如 `{"$.code": 0}` 或 `{"status_code": 200}`），正常用例再补关键字段（如 `{"$.code": 0, "$.data.role": "admin"}`）；异常用例断言错误码/信息（如 `{"$.code": 400, "$.msg": "password 不能为空"}`）。target 以 `$.` 开头表示取响应体字段，`status_code` 表示 HTTP 状态码。
5. **SQL 校验 `sql`**（字符串）：接口会改库的（写/改/删/状态流转）**必须**写 SELECT 去库里查数据校验，多条用 `;` 分隔，可引用 `${变量}`，表名/字段按接口语义推断。例：`"SELECT status FROM orders WHERE id = ${orderId}; SELECT qty FROM orders WHERE id = ${orderId}"`。删除类查 `COUNT(*)` 期望 0。不涉及库改动写 ""。
6. **变量贯通（重要）**：有前置依赖（如先登录拿 token、先建数据拿 id）时——依赖请求在它自己用例的 `extract` 里声明变量，后续用例才能在 `headers`/`body`/`path`/`sql` 里用 `${变量}` 引用。**每个被引用的 `${变量}`，都必须能在前面某条用例的 `extract` 里找到同名定义**；找不到来源的就别用变量、直接写具体值。批量按顺序执行，所以保证「登录用例」排在需要 token 的用例之前。
6.1. **禁止把可由接口准备的数据只写成前置条件（非常重要）**：
   - 不要写「前置条件：数据库已存在用户名为 xxx 的账号 / 已存在订单 / 已存在项目」这种不可执行假设。
   - 如果测试点依赖账号、订单、项目、资源 ID 等业务数据，必须优先生成对应的**创建/注册/登录**接口用例，并在 `extract` 里提取后续要用的变量；后续用例通过 `${变量}` 引用。
   - 如果本批测试点没有显式包含创建依赖，但后续测试点必须依赖它，也要补一条必要的前置 API 用例，名称用「准备xxx数据」或「创建xxx成功并提取id」，并排在依赖用例前。
   - 只有外部系统状态、服务启动、配置中心变量这类无法通过接口准备的内容，才允许放入 `preconditions`。
6.2. **执行顺序**：输出数组顺序就是写入后的默认执行顺序。先登录/创建/准备数据，再查询/更新/删除；正向链路先跑，参数异常/鉴权异常/边界用例放在相关正向用例之后。
7. **描述字段**：`preconditions`/`steps`/`expected` 仍各给字符串数组供人阅读（steps 简述请求、expected 简述校验点），不要加序号前缀。核心仍是上面的结构化字段。`preconditions` 不得替代可执行的 API 准备步骤。
8. **name 简短**，不带方法/路径前缀。
8.1. **category**：原样照抄该测试点的类别，只能是这组之一：`前置链`/`正常`/`响应校验`/`参数校验`/`边界`/`鉴权`/`越权`/`安全`/`场景`/`关联`/`其它`。**只填类别词本身，不要自己往 name 里加**——系统会自动把它拼成「【类别】用例名」。账号准备链填 `前置链`、安全用例填 `安全`、端到端业务流填 `场景`、跨模块依赖填 `关联`，不要混进「其它」。

8.2. **前置链用例（仅在本批测试点里出现 `前置链` 类别时才生成；本身是登录/认证接口的模块不要生成前置链）**：准备一个**可复用且可安全改动**的测试账号并登录。
   - **如果被测接口本身就是登录/认证接口**（本批就在测 login/token/注册），**不要再造前置链**——直接让"正常登录成功"用例 `extract` 出 `token`，后面的 `/me`、`/password` 等用例引用 `${token}` 即可。
   - **前置链是唯一允许跨模块的用例**：当本模块用例需要登录态、但本模块不含登录接口时，前置链**可以调用其它模块的"建账号/注册"接口**（见上面跨模块上下文里【可用的"建账号/注册"接口】，或接口文档/digest 里的注册接口）。其它非前置链用例仍按本模块接口正常生成，不要跨模块。
   - **有创建账号接口（含跨模块的，如 `POST /api/users`）** → 前置链用一条**多步 `requests`**：① 调创建接口建账号——**先判断该接口要不要鉴权：看 digest / 上方「API 约定」，若它需要认证或管理员权限（典型信号：无 token 调用会 401），就前置先用 `${my_account}/${my_password}` 登录拿 token、建号请求带 `Authorization: Bearer ${token}`；若是开放注册接口（谁都能建）则直接建、不带 token**——用户名 `function:unique(AUTO_TEST_user)`、**密码写固定字面量如 `Test@123`**、**并按接口文档 / digest / 上方「API 约定」补齐该接口声明的其它必填字段（有的系统建号要角色、类型、状态等字段，漏填会 422）**；**关键**：`function:unique` 每次解析都不同，所以**必须从创建响应里 `extract` 出真实用户名**（如 `{"test_user_id": "$.data.id", "test_username": "$.data.username"}`），登录才用得上。② 用 `${test_username}` + 固定密码 `Test@123` 登录该新账号，`extract` 出**本用例专属的** token（如 `own_token`，按真实层级，如 `{"own_token": "$.data.token"}`）。**绝不能第一步建新号、后面却登录 admin/共享账号**——那 token 就不是新账号的了，没意义。这样产出的是**专属测试账号的 token**，可被改密/删除而不影响系统。
   - **没有创建账号接口**（系统不开放注册，只能用现有账号）→ 前置链只做一步：用变量池账号 `${my_account}/${my_password}` 登录拿 `token`。**此时要诚实**：它和普通登录的唯一区别就是"登录一次、token 全模块共享复用"。这种情况下，**改密码/删除账号这类会破坏账号的用例没法用共享账号做**——必须在那条用例内用创建接口临时建号（见 8.3），否则就不要生成"改密码成功"这类用例。
   - 🚫 **致命错误，绝不能犯**：`function:unique(...)` 只是生成一个**新用户名**，这个账号**还不存在**。**绝不能拿一个 `function:unique` 用户名直接去调登录接口**——它没被创建过，登录必然 401。`function:unique` 账号**只能**先用"创建账号接口"(`POST /api/users` 之类)建出来、之后才能登录。**手里没有创建账号接口时，就老老实实用 `${my_account}/${my_password}` 登录，不要造 function:unique 账号**。
   - **"创建账号"步骤必须调真正的创建接口**（向 `/api/users`、`/register` 这种创建路径 POST），**不是登录接口**。登录接口只能认证"已存在"的账号，绝不会"顺便把账号建出来"。一个步骤名字叫"创建测试账号"、却把请求打到 `/api/auth/login`，是错的。
   - **断言只校验 `status_code` 为 200 即可**，它的职责就是把账号和 token 准备好。
   - 产出的 `token`/`test_user_id` 进共享变量池，**后续需要登录态的用例直接 `${token}` 引用，不要各自重新登录**。

8.3. **写操作类用例（改密码/删除/改角色等）绝不动共享账号**：两个原因——① 很多系统的**内置/共享管理员账号是受保护的**，对它改密码/删除会被拒（具体受保护账号名、拒绝状态码以项目契约/文档为准，如某些系统 403）；② 更普遍、更致命的是——**用共享账号做破坏性操作会污染后续所有用例**（改坏它，后面登录它的用例全部级联失败）。要测"改密码成功"这类，**必须在本用例内先用创建账号接口建一个一次性账号、登录它、再对它改密码**（一条多步 `requests` 闭环），改完可用 `teardown_sql` 删掉。如果系统没有创建账号接口，就生成不了真正的"改密码成功"用例——只保留"改共享/受保护账号密码被拒""未带 token 改密码被拒(401)"这类负向用例。

8.3.1. **⚠️ 会话作废类用例必须完全自我隔离（测试污染头号杀手）**：任何**会使 token / 会话失效**的操作——**登出（logout）、登出所有会话（logout-all）、刷新后旧 token 失效（refresh）、改密码导致旧 token 失效、停用/删除账号**——都**绝对不允许作用在共享 `${token}` 或 admin 上**。因为批量按顺序执行，一旦共享 token 被登出，后面所有引用 `${token}` 的用例全部 401「会话已失效」级联失败（连"建一次性账号"这步用共享 token 鉴权也会 401，自我隔离都启动不了）。
   - **铁律：共享 `${token}` 是只读凭证，任何用例都不得把它登出/刷新/改密/作废。**
   - 正确写法——**一条多步 `requests` 自带完整生命周期**：① 用创建账号接口建一次性账号（`function:generate_account` / `unique`，密码固定字面量），从响应 `extract` 出真实用户名；② 登录该新账号，`extract` 出**本用例专属的** token（如 `own_token`）；③ 对 `own_token` 执行登出/登出所有/刷新/改密；④ 断言用 `own_token` 再访问受保护接口返回 401（验证"确实失效了"）。**全程只碰 `own_token`，绝不碰 `${token}`。**
   - 若系统无创建账号接口（无法建一次性号）→ 这类"登出成功后 token 失效"的正向用例**没法安全生成**，只保留"无 token 登出被拒(401)""无效 token 登出被拒"这类**不破坏任何有效会话**的负向用例。

8.4. **登录/认证接口的参数校验别瞎测边界（重要）**：登录接口**只比对凭据、不校验用户名/密码的格式长度类型**。所以**不要**为登录生成"username 长度 1 字符 / 超长 / 纯空格 / 数字类型 / 特殊字符"这类用例——这些非法用户名只会因"查无此用户"返回 401，**测不出任何边界逻辑、而且没有对应账号必定失败、毫无意义**。登录的参数校验**只覆盖**：必填字段缺失、为空、null、密码错误、账号不存在。username/password 的长度/格式/类型/特殊字符边界，**只在"注册/创建用户"接口上测**（那里服务端才真的校验）。

8.5. **总原则：每条负向/边界用例都要"能有意义地失败"**——要么服务端确实对该输入做校验并返回特定错误码，要么有对应的前置数据让它有意义。如果一条用例无论如何都只会撞上同一个泛化错误（如登录一律 401），就不要生成它。

8.6. **边界临界值必须取自文档真实约束，不要猜**：写"等于最小/最大长度""刚好超界"这类用例时，临界值要用 digest/文档里该字段的真实 `min_length`/`max_length`/数值范围/枚举（如密码 `min_length=1` → 最小边界就是 1 个字符，不是想当然的 6/8）。**文档没声明的约束不要造**（没写复杂度就别测"必须含大小写数字"、没写长度上限就别测"超长被拒"）。另外"边界…修改/创建成功"这类**成功向边界用例同样需要完整前置**（非 admin 账号 + 有效 token + 正确旧密码），按 8.3 在用例内建一次性账号闭环，否则成功不了。
9. **after**（插入位置）：= 现有某条用例的完整名称（原样照抄）排其后；最前写 `"__START__"`；末尾写 `""`。

10. **场景用例（category=场景）用多步 `requests` 字段**：端到端业务流是**一条用例、多步请求**。给一个 `requests` 数组，每个元素 = 一次接口调用 `{name, method, path, headers, body, extract, assertion, sql}`（字段含义同上）。数组顺序就是执行顺序，前一步 `extract` 的变量后一步可直接 `${变量}` 引用。**有 `requests` 时，顶层的 method/path/headers/body/extract/assertion 全部省略、不要再填**（系统以 `requests` 为准；顶层再填会和步骤打架，导致展示/执行错乱）。非场景/非多步用例不要给 `requests`。
10.0. **每个 request 必须自洽：method+path+body 都是"这一个接口自己的"**。**绝不能把 A 接口的 path 配上 B 接口的 body**！例如改密码流程：建账号那步是 `POST /api/users` + `{username, password}`；改密码那步是 `PUT /api/auth/password` + `{old_password, new_password}`——**不能写成 `POST /api/users` + `{old_password, new_password}`**（路径是建用户、body 却是改密码，必然 422）。每一步都照着该接口在文档里的真实 path 和字段来。

10.1. **并发/性能/压测类测试点：本平台顺序执行，无法真并发**。**绝不能输出 method/path 全空的"空壳"用例**。遇到"并发多个登录验证稳定性/token 各自有效"这类点，改成**可执行的顺序多步 `requests`**，category 标 `场景`。**但必须尊重会话模型（关键，别写出必然失败的假用例）**：
    - **⚠️ 不要假设同一账号重复登录后旧 token 仍有效**。很多系统是**单会话模型**：同一账号每次登录都会踢掉上一次的会话，只有**最后一次**登录的 token 有效，之前的全部失效。若你把同一个账号顺序登录 3 次、再断言 `token1`/`token2` 仍能访问受保护接口，在单会话系统里**必然 401**——这是假用例。
    - **正确写法：用不同账号分别登录**。变量池里通常有多个账号（如 `${qa01_user}`/`${rd01_user}`/`${pm01_user}`/`${dev01_user}` 等），测"多会话各自有效"就让**不同账号各登一次**，每个 `extract` 各自 token（`token_qa`/`token_rd`/…），再各自带自己的 token 访问 `/me` 断言 200。不同账号的会话互不影响，这才是能通过、也真正验证了"多会话独立"的用例。
    - 若变量池只有一个可用账号、无法用多账号：**只保留"最后一次登录的 token 有效"**（断言最新 token 能访问、旧 token 已失效 401），不要断言旧 token 仍有效。
    - 若你不确定该系统是单会话还是多会话（项目上下文没写明），**按单会话这种更严格的假设写**（用不同账号，或只认最新 token），宁可保守也不要生成必然 401 的假用例。
    - **每条用例必须有可执行的 method/path 或 requests，不允许两者都空。**

11. **清理 `teardown`（强烈建议，配合数据治理）**：凡是**成功会在库里留下数据的写操作**（注册/创建/下单等正向用例），都应给 `teardown`——执行完（无论用例成败）自动清理刚建的数据，避免脏数据堆积。两种写法二选一或都给：
    - `teardown_api`：数组，每项是一次清理调用 `{method:"DELETE", path:"/api/orders/${orderId}", headers:{...}}`，引用本用例 `extract` 出的 id；**优先用真实删除接口**（顺带覆盖删除链路）。
    - `teardown_sql`：字符串，直接删库兜底，如 `"DELETE FROM orders WHERE id = ${orderId}"`，多条用 `;` 分隔。**只删本用例用 `AUTO_TEST_` 命名空间或 `${提取变量}` 定位的数据，绝不写无 WHERE 的全表删除、绝不删固定/种子数据**。
    - 逆向/查询/只读用例不产生残留，`teardown` 留空。

12. **data_safety（写操作用例附带）**：对写操作用例给一个对象，汇报你为数据安全做的处理：`{"policy":"一句话说明","rewritten_fields":["username->function:unique"],"readonly_seed_warnings":["..."],"function_hints":["..."],"cleanup_required":true}`。只读/逆向用例可省略或给 `{}`。

13. **会话隔离 `pre_hook`（登录/认证模块强烈建议）**：如果本批里存在**会作废会话的用例**（登出 / 登出所有会话 / 刷新令牌 / 改密码），那么共享 `${token}` 随时可能被它们杀掉，导致后面引用 `${token}` 的用例连环 401。为彻底免疫，给**每条需要有效登录态的用例**加一个 `pre_hook`：跑用例前先自己登录拿一个专属 token，不依赖共享 token。
    - 形态（数组，通常一条登录 hook）：`"pre_hook": [{"type":"http_request","config":{"method":"POST","path":"<真实登录路径>","params":{"username":"${my_account}","password":"${my_password}"},"extract_data":{"token":"<按真实响应,如 $.data.access_token>"}}}]`
    - `extract_data` 提取的变量名（如 `token`）要与本用例请求头 `Authorization: Bearer ${token}` 引用的一致。登录路径、账号变量、token 的 JSONPath 都照真实登录用例填，**不要猜**。
    - 用了 `pre_hook` 拿 token 的用例，**其请求头直接用 `${token}` 即可**，不必再排在某个登录用例之后（它自带登录，顺序无关）。
    - 非登录/认证模块、或本批没有会话作废类用例时，**不需要 `pre_hook`**——按常规共享 token 即可，别画蛇添足。

# 输出格式（严格 JSON，只输出一个 ```json``` 代码块，不要任何额外文字）

```json
[
  {
    "name": "准备测试账号并登录拿token",
    "category": "前置链",
    "after": "__START__",
    "requests": [
      {"name": "创建测试账号", "method": "POST", "path": "/api/users", "headers": {"Content-Type": "application/json"}, "body": {"username": "function:unique(AUTO_TEST_user)", "password": "Test@123"}, "extract": {"test_user_id": "$.data.id", "test_username": "$.data.username"}, "assertion": {"status_code": 200}},
      {"name": "登录新账号拿token", "method": "POST", "path": "/api/auth/login", "body": {"username": "${test_username}", "password": "Test@123"}, "extract": {"token": "$.data.token"}, "assertion": {"status_code": 200}}
    ],
    "preconditions": ["服务与数据库已启动"],
    "steps": ["注册测试账号→登录,提取共享 token/user_id 供后续用例复用"],
    "expected": ["两步均返回 200,token 提取成功"]
  },
  {
    "name": "创建订单成功并入库",
    "category": "正常",
    "after": "",
    "method": "POST",
    "path": "/api/orders",
    "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${token}"},
    "body": {"productId": 1001, "qty": 2, "remark": "function:unique(AUTO_TEST_order)"},
    "extract": {"orderId": "$.data.orderId"},
    "assertion": {"$.code": 0},
    "sql": "SELECT status FROM orders WHERE id = ${orderId}; SELECT qty FROM orders WHERE id = ${orderId}",
    "teardown_api": [{"method": "DELETE", "path": "/api/orders/${orderId}", "headers": {"Authorization": "Bearer ${token}"}}],
    "teardown_sql": "DELETE FROM orders WHERE id = ${orderId}",
    "data_safety": {"policy": "写入数据用 AUTO_TEST_ 命名空间，执行后删除", "rewritten_fields": ["remark->function:unique"], "cleanup_required": true},
    "preconditions": ["已先登录并提取 ${token}", "服务与数据库已启动"],
    "steps": ["POST /api/orders 带 ${token} 下单", "提取 orderId", "查库校验订单状态与数量"],
    "expected": ["响应 code=0、orderId 非空", "orders 表中该订单状态正确、qty=2"]
  },
  {
    "name": "注册登录下单支付完整链路",
    "category": "场景",
    "after": "",
    "requests": [
      {"name": "注册新用户", "method": "POST", "path": "/api/register", "headers": {"Content-Type": "application/json"}, "body": {"username": "function:unique(AUTO_TEST_user)", "password": "Test@123"}, "extract": {"userId": "$.data.id"}, "assertion": {"$.code": 0}},
      {"name": "登录拿 token", "method": "POST", "path": "/api/login", "body": {"username": "${my_account}", "password": "${my_password}"}, "extract": {"token": "$.data.token"}, "assertion": {"$.code": 0}},
      {"name": "下单", "method": "POST", "path": "/api/orders", "headers": {"Authorization": "Bearer ${token}"}, "body": {"productId": 1001, "qty": 1}, "extract": {"orderId": "$.data.orderId"}, "assertion": {"$.code": 0}}
    ],
    "teardown_sql": "DELETE FROM orders WHERE id = ${orderId}; DELETE FROM users WHERE id = ${userId}",
    "data_safety": {"policy": "全链路用 AUTO_TEST_ 数据，结束清理订单与用户", "cleanup_required": true},
    "preconditions": ["服务与数据库已启动"],
    "steps": ["注册→登录→下单 串联执行，变量逐步贯通"],
    "expected": ["每步 code=0，最终订单创建成功"]
  },
  {
    "name": "使用有效token访问受保护接口成功",
    "category": "正常",
    "after": "",
    "pre_hook": [{"type": "http_request", "config": {"method": "POST", "path": "/api/auth/login", "params": {"username": "${my_account}", "password": "${my_password}"}, "extract_data": {"token": "$.data.access_token"}}}],
    "method": "GET",
    "path": "/api/auth/me",
    "headers": {"Authorization": "Bearer ${token}"},
    "assertion": {"status_code": 200, "$.status": "success"},
    "preconditions": ["pre_hook 已自带登录，不依赖其它用例的 token"],
    "steps": ["pre_hook 先登录拿专属 token → GET /me 带该 token"],
    "expected": ["返回 200，能拿到当前用户信息（即使前面有登出用例也不受影响）"]
  }
]
```

> 上面最后一条演示了 `pre_hook`：它自带登录,所以哪怕前面有"登出/登出所有会话"用例把共享 token 作废了,它照样用自己新登录的 token 访问成功。登录/认证模块里凡是需要有效 token 的用例,都建议这样配。

约束：整个返回值是能被 `json.loads` 解析的合法 JSON 数组；不要在数组外写注释或解释。
