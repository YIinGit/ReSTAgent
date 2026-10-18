# ReSTAgent · 本地 AI 文稿审读

**软件名称：ReSTAgent**（浏览器标签页、启动窗口、Word 批注作者、后续 exe 与安装包同名，均为 `ReSTAgent`）

一个跑在自己电脑上的单机审稿小工具：**选 Word 稿 → 选环节与力度 → AI 按《文稿培训资料 V2.5.0》逐条给意见 → 逐条接受/改写/忽略 → 导出带修订与批注的 Word**。

- 不联网（除调用你自己配置的模型 API）、不上传云端、不改动原文件。
- 审稿标准来自《文稿培训资料 V2.5.0》：零审（录用把关）、一审（内容与结构）、二审（文字与语法）。
- 支持一次选择多份稿件排队审读。

---

## 一、快速开始

### 1. 启动

**桌面窗口（推荐）**：双击 **`ReSTAgent.bat`**，它会先自检（打印 Python 版本、端口、浏览器探测结果），通过后再打开一个没有地址栏、没有标签页的独立窗口；关掉窗口，服务即停。第一次可以运行 `make_shortcut.bat` 在桌面建一个带图标的快捷方式。

> 窗口用的是系统里已有的 Edge / Chrome 的“应用模式”，不额外装任何东西；找不到时退回默认浏览器 + 一个小的控制窗。

**浏览器模式（调试用）**：双击 `start.bat`，同样先自检，然后在前台起服务并打开 `http://127.0.0.1:5731`，窗口里能看到服务端日志。

> `ReSTAgent.pyw` 也能双击启动（无控制台），但它依赖 Windows 的 `.pyw` 文件关联。如果你的 `py` 启动器指向了一个已被卸载的 Python 版本，双击会弹 “Unable to create process…”，这种情况请改用 `ReSTAgent.bat`。

启动脚本会**逐个实测** `py -3.12 / py -3.13 / py -3.14 / py -3.11 / py -3.10 / python / python3 / py -3 / py`，用第一个能真正跑起来的解释器，所以 `py -0p` 里有失效条目也不影响。

也可以用命令行：

```bat
python -m app.desktop                      :: 桌面窗口
python -m app.desktop --selftest           :: 只自检，不开窗
python -m app.server --port 5731 --no-browser --log   :: 纯服务 + 请求日志
```

> 环境要求：Windows + Python 3.10 及以上。**不需要安装任何第三方包**（只用 Python 标准库）。
> 想打包成单个 exe，见 [packaging/README.md](<packaging/README.md>)。

### 2. 配置模型

点右上角 **设置**：

| 项 | 说明 |
|---|---|
| API Base URL | OpenAI 兼容地址，填到 `/v1` 为止，例如 `https://api.deepseek.com/v1`（末尾不用带 `/chat/completions`，带了也会自动去掉） |
| API Key | 存在本机 `config.json`，只发给上面这个地址 |
| 各力度使用的模型 | 默认 轻/标准 = `deepseek-chat`，严格/极致 = `deepseek-reasoner`（可改） |
| 单块字数 | 默认 2800。稿件很长时可调大以省调用，调小则定位更细 |
| 输出上限 max_tokens | 默认 32768；填 `0` 表示不传这个参数、用服务端默认（等于不设上限）。日志出现「输出被截断」就调大或填 0（程序也会自动放开上限重试一次） |
| 导出正文字体 | 默认「统一为宋体」（只改中文，西文数字不动）；可改回「跟随原稿」或换成仿宋/楷体 |

点「测试连接」确认可用：它会打印**实际请求的完整地址**、每一步的状态码，失败时给出针对性建议（404 会提示地址路径不对，并探测少写/多写 `/v1` 的备选地址；401 提示 Key 无效；400/422 提示模型名不对）。**没填 Key 也能用**：此时自动进入「本地规则预检」，只用正则与词表查硬性错误（直引号、半角标点、的/地/得、重复点号等）。

### 3. 审一次

1. 拖入或选择 `.docx`（可多选），也可以点「用示例稿件试一次」。
2. 选环节：**零审 / 一审 / 二审**（三审暂未开放，见文末）。
3. 选力度：轻 / 标准 / 严格 / 极致。
4. 点 **开始审稿**，看进度与日志。
5. 在结果页逐条处理：
   - **接受修改**：按替换文本落成 Word 修订；
   - **改写**：先在文本框里改，再接受；
   - **仅批注不改文**：只留批注，不动原文（适合需要和作者商量的地方）；
   - **忽略**：不写进导出文件。
   - 快捷键：`A` 接受、`X` 忽略、`↑ ↓` 切换，也可「接受全部必改」「忽略全部可选」。
6. 点 **导出 Word**（多份稿件可「全部导出」为 zip）。导出文件在 `data/exports/`，也可在结果页直接「用 Word 打开」。

---

## 二、导出的 Word 长什么样

导出是**新文件**，命名为 `原名_环节_时间.docx`，原稿一个字节都不动。

- **改动** → Word 修订痕迹（`w:ins` / `w:del`），作者打开后可用「审阅 → 接受/拒绝修订」逐条确认。
- **原因** → Word 批注，统一格式：

  ```
  【必改·标点】引号
  问题：使用了直引号（傻瓜引号），排版中禁止使用。
  建议：改为中文弯引号“”。
  改为：“这就是自由基”
  依据：第十讲·引号；第十一讲·第六节
  ```

- 作者栏为「ReSTAgent」（可在 `config.json` 的 `review.author` 改）。
- 未接受、未处理的意见不会写进文档。

---

## 三、三个环节各自做什么

| 环节 | 依据 | 查什么 | 产出 |
|---|---|---|---|
| **零审** | 第二讲·稿件收集、第三讲·分级 | 属性（科普/科幻/科协）、立论、内容密度、敏感、难度、论文/百科体例 | 录用结论（可录用/修改后录用/退稿）+ 建议分级与板块 + 理由 |
| **一审** | 第六讲、第五讲、第七讲 | 细节敏感、学术/知识错误、数据精确性、结构（虎头蛇尾/逻辑顺序/节奏）、文风、术语密度 | 逐条意见 + 结构建议 + 配图建议 |
| **二审** | 第八、九、十讲 | 病句六类、错别字（的/地/得、易错字表）、近义词、术语规范、数字与西文、中西混排、标点 | 逐条可替换的修改 |

四档力度：

| 力度 | 行为 |
|---|---|
| 轻 | 单遍，只报必改，最多 12 条 |
| 标准 | 全规则单遍（一审另加整体结构通读） |
| 严格 | 逐块 + 复核轮（跨段一致性、重复错误、漏网问题），建议用推理模型 |
| 极致 | 二审双视角并行（语言规范 / 读者理解）+ 复核；一审加反向质疑 |

---

## 四、规则怎么维护

规则是可读的 Markdown，放在 `app/rules/`：

```
app/rules/zero_stage.md     零审判据、分级字数区间
app/rules/first_stage.md    一审检查清单（含教材里的反例）
app/rules/second_stage.md   二审：病句六类、错字表、近义词表、标点硬规则
```

改完保存即生效（无需重启，规则按需读取）。每条意见都会带 `依据：第 X 讲·…`，方便倒查是哪条规则报的。

本地预检用的正则与词表在 `app/review/quick_checks.py`。

---

## 五、目录结构

```
ReSTAgent/
├─ ReSTAgent.pyw          双击启动桌面窗口（无控制台）
├─ start.bat              浏览器模式启动（带日志，调试用）
├─ make_shortcut.bat      在桌面建快捷方式
├─ packaging/             PyInstaller 配置与打包说明
├─ config.json            你的配置（首次启动生成）
├─ app/
│  ├─ desktop.py          桌面窗口启动器（--app 模式 + 兜底控制窗）
│  ├─ server.py           本地 HTTP 服务
│  ├─ jobs.py             任务与导出管理
│  ├─ config.py           配置
│  ├─ paths.py            源码/打包两种运行方式的路径约定
│  ├─ docx_engine/        文档解析 / 定位 / 批注与修订注入（纯标准库）
│  ├─ review/             提示词、流水线、模型客户端、本地预检
│  └─ rules/              审稿规则与词表
├─ web/                   界面（无框架、无构建）+ 图标
├─ data/
│  ├─ uploads/            上传的稿件
│  ├─ exports/            导出的审稿文件
│  └─ sessions/           每次审稿的记录（JSON）
├─ samples/示例稿_待审.docx
├─ tools/                 dump_docx / make_icon / make_shortcut
└─ tests/                 回归测试与脚本
```

---

## 六、测试

```bat
python tests\make_sample.py          :: 生成基础样例
python tests\make_demo.py            :: 生成示例稿（samples/）
python tests\test_export.py          :: 批注/修订注入
python tests\test_pipeline.py        :: 离线流水线 + 导出闭环
python tests\test_llm.py             :: 用假 OpenAI 服务验证客户端、JSON 容错、多轮
python tests\test_server.py          :: 接口端到端（上传→审稿→决定→导出→下载）
python tests\test_demo_export.py     :: 示例稿全量导出 + 结构校验
python tests\test_reexport.py        :: 在已导出的文件上再导出（二次审读）
python tests\test_export_font.py     :: 导出字体统一（docDefaults / Normal / 正文 run）
python tests\test_probe.py           :: 连接诊断（URL 归一、404 提示、备选地址探测）
python tests\test_desktop.py         :: 桌面启动器（自检、兜底、单实例）
python tests\validate_docx.py 文件.docx   :: 单独体检任意导出的 docx
```

`validate_docx.py` 是防御 Word 报「无法读取的内容」的那道闸，检查的都是会让 Word 直接拒开的硬伤：
根元素命名空间声明是否覆盖 `mc:Ignorable`、`w:pPr`/`w:rPr` 是否在首位、
批注 start/end/reference 是否一一对应且闭合在同段内、修订 id 是否重复、
`w:del` 里是否混进了 `w:t`、`core.xml` 是否有重复元素、内容类型与关系是否完整、包内是否有重复条目。

---

## 七、已知限制

1. **不联网查证**：学术错误只能给「存疑、建议核实」，不冒充权威、不编造出处（符合教材"多与作者沟通"的原则）。后续可加搜索接口。
2. **三审未开放**：教材里的三审是查排版 PDF（图文遮挡、边距、单字成行），Word 文件里没有可靠的版面信息，需要 PDF 渲染 + 视觉模型，成本与复杂度另算。
3. **定位精度**：意见通过「段落号 + 原文片段」锚定。原文片段在段落里找不到时会降级为整段批注（导出统计里会体现），极少数超链接、域代码中的文字会走降级。
4. **长文**：按块审读（默认 2800 字/块），块与块之间靠标题大纲与相邻段上下文衔接；跨块的深层逻辑问题要靠「严格/极致」的复核轮。
5. **模型能力差异**：同一套提示词在不同模型上召回率差异明显；中文标点与病句建议用能力较强的模型跑二审。

---

## 八、打不开怎么办

**现象一：双击后黑窗口一闪就没**

在 `cmd` 里进到目录手动跑 `start.bat`，就能看到卡住的原因。最常见的两种：

- 找不到 Python：脚本会列出试过的解释器，并提示 `py -0p`。
- `py` 指向的版本已被卸载：典型报错是
  `Unable to create process using 'C:\Users\<你>\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe'`。
  这是**系统级的 py 启动器登记失效**，与程序本身无关。处理办法任选其一：
  1. 直接用 `ReSTAgent.bat`（本程序的启动脚本会自己实测可用解释器，绕开失效的 py 登记）；
  2. 运行 `py -0p` 看清单，重装或修复被删掉的那个 Python 版本；
  3. 把能用的 `python.exe` 目录加到 PATH。

**现象二：双击 `ReSTAgent.pyw` 弹 “Unable to create process”**

同上是 `.pyw` 文件关联指向了失效的 py 版本。改用 `ReSTAgent.bat` 即可。

**现象三：窗口打开了但页面空白 / 提示连不上**

看 `desktop.log`（源码运行时在程序目录，打包后在 `%LOCALAPPDATA%\ReSTAgent\`）；或改用 `start.bat`，它会在控制台里直接打印服务端日志。

**现象四：端口被占用**

服务默认 5731，被占用会自动顺延（5732、5733…），启动日志里会打印实际地址。

**现象五：Word 打开导出文件时提示「发现无法读取的内容，是否恢复」**

这是 0.1 早期版本导出器的缺陷，**已在当前版本修复**，原因有两个：

1. 写回 `word/document.xml` 时用了 ElementTree 重新序列化，它把根元素上「只被 `mc:Ignorable` 引用、元素里没用到」的 `xmlns:w14` / `xmlns:wp14` 声明丢掉了，留下悬空前缀；
2. `docProps/core.xml` 里原本就有一个空的 `<cp:lastModifiedBy/>`（自闭合），旧代码的正则只认成对标签，于是又追加了一个，导致同一属性元素出现两次。

现在前者会把原文档根元素上的全部 `xmlns` 声明原样补回，后者改为就地赋值。**旧版本导出的文件请删掉重新导出**；新导出可以用 `python tests\validate_docx.py 文件.docx` 自查（会检查上述两类问题以及批注区间、修订 id、内容类型等硬伤）。

> 注意：**改完代码必须重启程序**（关掉应用窗口，重新双击 `ReSTAgent.bat`）。Python 不会热加载，旧进程会继续用旧代码导出，看起来就像"改了没生效"。

**现象六：审读时报 `HTTP 404`，返回体是一段 nginx 的 404 页面**

说明 `/chat/completions` 这个路径在服务商的域名下不存在，问题在 Base URL 而不是 Key：

1. 点「测试连接」，它会打印本次请求的**完整地址**和每一步状态码；
2. 对照服务商文档，Base URL 一般写到 `/v1` 为止（例如 `https://域名/v1`），不要自己拼 `/chat/completions`；
3. 若测试结果里出现「探测到另一个可用前缀：…」，把那个地址填进 Base URL 再测；
4. 404 与 Key 无关；401/403 才是 Key 的问题。

---

## 九、打包成桌面应用

三种形态，详见 [packaging/README.md](<packaging/README.md>)：

1. **独立窗口（已可用，零依赖）**：`ReSTAgent.bat` → 服务 + Edge/Chrome 应用窗口，关窗即停；
2. **单文件 exe**：`packaging\build_exe.bat`（PyInstaller，产物 `dist\ReSTAgent.exe`，无控制台）；
3. **原生 WebView2 壳**：可选，`pip install pywebview`，代码见 packaging/README.md。

打包后配置与数据写到 `%LOCALAPPDATA%\ReSTAgent\`（`config.json`、`data\`、`desktop.log`），升级 exe 不丢配置；源码运行时仍写在项目目录，两套路径由 `app/paths.py` 统一判断。

命名统一：窗口标题与产品名 `ReSTAgent`，exe `ReSTAgent.exe`，安装包 `ReSTAgent-Setup-x.y.exe`。名称与版本号只在 `app/__init__.py` 的 `APP_NAME` / `APP_VERSION` 改一处。
