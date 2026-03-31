// script.js
const API_BASE = "http://127.0.0.1:54351/api";
// 统一使用 window 对象挂载，确保全局唯一性
window.currentProjectId = null;
window.currentParentId = null;
window.isEditMode = false;
window.currentEditId = null;
window.currentListData = [];
window.insertIndex = null;

// --- 路由与视图控制 ---
const router = {
    // 1. 首页：展示所有项目
    async home() {
        window.currentProjectId = null;
        window.currentParentId = null;
        const projects = await fetch(`${API_BASE}/projects`).then(r => r.json());
        let html = `<h1>欢迎使用自动化测试平台</h1>`;
        if (projects.length === 0) {
            html += `
                <div style="margin-top:50px; text-align:center;">
                    <p>目前还没有项目，请先创建。</p>
                    <button class="btn btn-primary" onclick="openProjectModal('API')">创建第一个项目</button>
                </div>`;
        } else {
            html += `<div class="project-grid">`;
            projects.forEach(p => {
                html += `
                    <div class="project-card" onclick="router.projectDetail(${p.id}, null)">
                        <div style="font-size:2.5rem; margin-bottom:10px;">${p.icon || '📁'}</div>
                        <h3>${p.name}</h3>
                        <p>${p.description || '暂无简介'}</p>
                        <span class="project-type">${p.type}</span>
                    </div>`;
            });
            html += `</div>`;
        }
        render(html);
    },

    // 2. 项目管理：按类型（API/Web/Mobile）过滤
    async projectList(type) {
        window.currentProjectId = null;
        const allProjects = await fetch(`${API_BASE}/projects`).then(r => r.json());
        const filtered = allProjects.filter(p => p.type === type);

        let html = `
            <div class="header-actions">
                <h2>${type} 自动化项目管理</h2>
                <button class="btn btn-primary" onclick="openProjectModal('${type}')">+ 新增项目 ${type}</button>
            </div>
            <div class="project-grid">`;

        if (filtered.length === 0) {
            html += `<p style="grid-column: 1/-1; text-align: center; padding: 40px;">暂无 ${type} 项目。</p>`;
        } else {
            filtered.forEach(p => {
                html += `
                    <div class="project-card">
                        <div onclick="router.projectDetail(${p.id}, null)" style="cursor:pointer">
                            <div style="font-size:2rem;">${p.icon || '📁'}</div>
                            <h3>${p.name}</h3>
                            <p>${p.description}</p>
                        </div>
                        <div style="margin-top:15px; border-top:1px solid #eee; padding-top:10px; display:flex; gap:5px; justify-content:center;">
                            <button class="btn btn-primary" style="font-size:12px; padding:4px 8px;" onclick="editProject(${p.id})">编辑</button>
                            <button class="btn btn-danger" style="font-size:12px; padding:4px 8px;" onclick="deleteProject(${p.id})">删除</button>
                            <button class="btn btn-primary" style="font-size:12px; padding:4px 8px; background:#f39c12" onclick="runProjectCases(${p.id})">执行</button>
                        </div>
                    </div>`;
            });
        }
        html += `</div>`;
        render(html);
    },

    // 3. 进入项目/模块详情
    async projectDetail(projId, moduleId = null) {
        if (moduleId === "null" || moduleId === "undefined") moduleId = null;
        // 1. 统一更新全局状态
        window.currentProjectId = projId;
        window.currentParentId = moduleId;

        try {
            let parentIdForBack = null;
            let displayName = ""; // 用于显示在面包屑上的名称

            if (moduleId) {
                const modRes = await fetch(`${API_BASE}/modules/${moduleId}`);
                if (modRes.ok) {
                    const modData = await modRes.json();
                    parentIdForBack = modData.parent_id;
                    displayName = `📂 ${modData.name}`;
                }
            } else {
                // 场景 B：在项目根目录，获取项目详情
                const projRes = await fetch(`${API_BASE}/projects/${projId}`);
                if (projRes.ok) {
                    const projData = await projRes.json();
                    displayName = `🚀 ${projData.name}`;
                }
            }

            let contentUrl = `${API_BASE}/content/${projId}`;
            if (moduleId) contentUrl += `?parent_id=${moduleId}`;
            const res = await fetch(contentUrl);
            const data = await res.json();

            window.currentListData = data;

            let html = `
                <div class="header-actions" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <div class="breadcrumb">
                        <button class="btn btn-ghost" onclick="window.router.home()">🏠 首页</button>
                        <span style="margin: 0 5px; color: #ccc;">/</span>
                        ${moduleId ?
                            `<button class="btn btn-ghost" onclick="window.router.projectDetail(${projId}, ${parentIdForBack})">⬅️ 返回上一级</button>` :
                            ''
                        }
                        <span style="margin-left:10px; font-weight:bold; color:var(--accent-color)">${displayName}</span>
                    </div>
                    <div>
                        <button class="btn btn-primary" onclick="openModuleModal()">+ 新增模块</button>
                        ${moduleId !== null ?
                            `
                            <button class="btn btn-ghost" onclick="document.getElementById('import-modal').style.display='flex'">📥 导入用例</button>
                            <button class="btn btn-primary" style="background:#27ae60; margin-left:10px;" onclick="newCaseModal()">+ 新增用例</button>` :
                            ''
                        }
                    </div>
                </div>
                <table class="main-table">
                    <thead>
                        <tr>
                            <th width="40"><input type="checkbox" id="selectAll"></th>
                            <th width="80">执行顺序</th>
                            <th width="120">名称</th>
                            <th width="120">请求地址</th>
                            <th width="150">请求类型</th>
                            <th width="150">请求参数</th>
                            <th width="150">操作</th>
                        </tr>
                    </thead>
                    <tbody id="sortable-list">`;

            if (data.length === 0) {
                html += `<tr><td colspan="6" style="text-align:center; padding:30px;">该目录下暂无内容</td></tr>`;
            } else {
                data.forEach((item, index) => {
                    const isMod = item.type === 'module';
                    const projId = window.currentProjectId;
                    const belongsToModuleId = isMod ? item.id : (item.module_id || window.currentParentId);

                    const clickAction = isMod
                        ? `window.router.projectDetail(${projId}, ${item.id})`
                        : `toggleCasePreview(${item.id})`;

                    const icon = isMod ? '📁' : '📄';

                    html += `
                        <tr class="draggable" data-id="${item.id}" data-type="${item.type}" draggable="true">
                            <td><input type="checkbox" class="item-checkbox" value="${item.id}"></td>
                            <td style="color: #888; font-family: monospace;">${index + 1}</td>
                            
                            <td onclick="${clickAction}"
                                style="cursor:pointer; ${isMod ? 'font-weight:bold; color:#3498db;' : 'color:#2c3e50;'}">
                                <span style="margin-right: 5px;">${icon}</span>
                                ${item.name}
                                ${!isMod ? '<small style="color:#999;margin-left:8px;">(点击预览)</small>' : ''}
                            </td>
                            
                            <td>${isMod ? '-' : (item.path || '/')}</td>
                            
                            <td><span class="badge">${isMod ? '-' : (item.method || 'GET')}</span></td>
                            
                            <td>
                                <small style="color:#999;">${isMod ? '-' : (item.params ? 'JSON...' : '{}')}</small>
                            </td>
                            
                            <td>
                                <button class="btn btn-success" style="font-size:12px; padding:2px 6px; background-color: #27ae60; color: white;" 
                                    onclick="runTestHandler(${projId}, ${belongsToModuleId}, ${isMod ? 'null' : item.id})">
                                    ▶ 运行
                                </button>
                            
                                <button class="btn btn-primary" style="font-size:12px; padding:2px 6px;"
                                    onclick="${isMod ? `editModule(${item.id})` : `handleEditCase(${item.id})`}">编辑</button>
                                
                                ${!isMod ? `
                                <button class="btn btn-ghost" style="font-size:12px; padding:2px 6px; border:1px solid #ddd;" 
                                    onclick="insertCaseAfter(${index})">插入</button>
                                ` : ''}
                            
                                <button class="btn btn-danger" style="font-size:12px; padding:2px 6px;"
                                    onclick="confirmDelete('${item.type}', ${item.id})">删除</button>
                            </td>
                        </tr>
                        ${!isMod ? `
                        <tr id="preview-row-${item.id}" style="display:none; background-color: #f9f9f9;">
                            <td colspan="8">
                                <div style="padding: 20px; border-left: 5px solid #3498db; margin: 10px 20px; background: white; border-radius: 4px; box-shadow: inset 0 0 10px rgba(0,0,0,0.05);">

                                    <div style="margin-bottom: 15px; border-bottom: 1px dashed #ddd; padding-bottom: 10px;">
                                        <strong style="color:#555;">📝 用例描述:</strong>
                                        <span style="color:#333; margin-left:10px;">${item.description || '暂无描述'}</span>
                                    </div>

                                    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 15px;">
                                        <div>
                                            <strong style="color:#555;">🔗 请求头 (Headers):</strong>
                                            <pre style="background:#f4f4f4; padding:8px; border-radius:4px; font-size:12px; margin-top:5px;">${formatAndHighlight(item.headers)}</pre>
                                        </div>
                                        <div>
                                            <strong style="color:#555;">🔍 提取参数 (Extract):</strong>
                                            <pre style="background:#f4f4f4; padding:8px; border-radius:4px; font-size:12px; margin-top:5px;">${formatAndHighlight(item.extract_data)}</pre>
                                        </div>
                                        <div>
                                            <strong style="color:#555;">📁 文件路径:</strong>
                                            <div style="background:#f4f4f4; padding:8px; border-radius:4px; font-size:12px; margin-top:5px; color:#e83e8c;">
                                                ${item.file_path || '无'}
                                            </div>
                                        </div>
                                    </div>

                                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                                        <div>
                                            <strong style="color:#555;">📤 请求参数 (Payload):</strong>
                                            <pre style="background:#2d2d2d; color:#ccc; padding:10px; border-radius:4px; font-size:12px; margin-top:5px; max-height:200px; overflow:auto;">${formatAndHighlight(item.params)}</pre>
                                        </div>
                                        <div>
                                            <strong style="color:#555;">🗄️ 预置 SQL:</strong>
                                            <pre style="background:#2d2d2d; color:#7ec699; padding:10px; border-radius:4px; font-size:12px; margin-top:5px; max-height:200px; overflow:auto;">${formatAndHighlight(item.sql_query)}</pre>
                                        </div>
                                    </div>

                                    <div style="margin-top: 15px; font-size: 12px; color: #888;">
                                        <span>⏱️ 等待时间: <b style="color:#333">${item.wait_time || 0}s</b></span>
                                        <span style="margin-left: 20px;">✅ 断言校验: <b style="color:#28a745">${item.assertion || '默认 200'}</b></span>
                                    </div>
                                </div>
                                </td>
                        </tr>` : ''}
                        `;
                });
            }

            html += `</tbody></table>`;

            // 4. 渲染到页面
            document.getElementById('view-container').innerHTML = html;

            // 5. 初始化拖拽
            if (typeof initDragging === 'function') initDragging();

        } catch (err) {
            console.error("加载详情失败:", err);
            alert("无法加载项目内容，请检查网络或后端接口。");
        }
    }
};

// --- 功能函数 ---
function render(content) {
    document.getElementById('view-container').innerHTML = content;
}

function toggleSubNav(id) {
    const el = document.getElementById(id);
    el.style.display = el.style.display === 'block' ? 'none' : 'block';
}

function openProjectModal() {
    window.isEditMode = false;
    window.currentEditId = null;

    // 清空表单
    document.getElementById('p-name').value = '';
    document.getElementById('p-desc').value = '';
    document.getElementById('modal-title').innerText = "新增项目";

    document.getElementById('project-modal').style.display = 'flex';
}

async function editProject(id) {
    window.isEditMode = true;
    window.currentEditId = id;
    const res = await fetch(`${API_BASE}/projects/${id}`);
    const p = await res.json();

    document.getElementById('modal-title').innerText = "编辑项目";
    document.getElementById('p-name').value = p.name || '';
    document.getElementById('p-desc').value = p.description || ''; // 确保后端返回的是 description
    document.getElementById('p-type').value = p.type || 'Web';
    document.getElementById('p-icon').value = p.icon || '📁';
    document.getElementById('project-modal').style.display = 'flex';
    }

async function deleteProject(id) {
    if (!confirm("确定要删除该项目吗？这将删除项目下的所有模块和用例！")) return;

    try {
        const res = await fetch(`${API_BASE}/projects/${id}`, { method: 'DELETE' });
        if (res.ok) {
            router.home(); // 删除后返回首页
        } else {
            alert("删除失败");
        }
    } catch (e) {
        console.error("删除项目异常:", e);
    }
}

function openModuleModal() {
    window.isEditMode = false;
    window.currentEditId = null;
    document.getElementById('m-name').value = '';
    document.getElementById('module-modal').style.display = 'flex';
}

// --- 编辑模块 ---
async function editModule(moduleId) {
    window.isEditMode = true;
    window.currentEditId = moduleId;
    // 获取详情
    const res = await fetch(`${API_BASE}/modules/${moduleId}`);
    const m = await res.json();

    document.getElementById('m-name').value = m.name;
    document.getElementById('module-modal').style.display = 'flex';
}

// --- 模块-用例统一删除分发逻辑 ---
async function confirmDelete(type, id) {
    const typeName = type === 'module' ? '模块' : '测试用例';
    if (!confirm(`确定要删除该${typeName}吗？此操作不可恢复！`)) return;

    // 根据类型动态拼接 URL
    const endpoint = type === 'module' ? 'modules' : 'test_cases';

    try {
        const res = await fetch(`${API_BASE}/${endpoint}/${id}`, {
            method: 'DELETE'
        });

        if (res.ok) {
            // 删除成功后，刷新当前项目详情页
            window.router.projectDetail(window.currentProjectId, window.currentParentId);
        } else {
            const err = await res.json();
            alert(`删除失败: ${err.detail || '服务器错误'}`);
        }
    } catch (e) {
        console.error("删除请求异常:", e);
        alert("网络异常，请稍后再试");
    }
}


function closeModal(id) {
    document.getElementById(id).style.display = 'none';
}

async function submitProject() {
    const name = document.getElementById('p-name').value;
    const desc = document.getElementById('p-desc').value;
    const type = document.getElementById('p-type').value;
    const icon = document.getElementById('p-icon').value;
    const payload = { name, description: desc, type, icon };
    // 【关键修改点】判断是编辑还是新增
    const url = window.isEditMode
        ? `${API_BASE}/projects/${window.currentEditId}`
        : `${API_BASE}/projects`;
    const method = window.isEditMode ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            closeModal('project-modal');
            router.home(); // 刷新页面
        }
    } catch (e) {
        console.error("提交失败:", e);
    }
}

async function submitModule() {
    const name = document.getElementById('m-name').value;
    const payload = {
        name: name,
        project_id: window.currentProjectId,
        parent_id: window.currentParentId
    };
    const url = window.isEditMode
        ? `${API_BASE}/modules/${window.currentEditId}`
        : `${API_BASE}/modules`;
    const method = window.isEditMode ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            closeModal('module-modal');
            router.projectDetail(window.currentProjectId, window.currentParentId);
        }
    } catch (e) {
        console.error(e);
    }
}

// 提交用例
async function submitCase() {
    // 1. 获取全局存储的当前模块ID
    const mId = window.currentParentId;
    // 2. 校验：必须在模块内才能创建用例 (根目录下 moduleId 为 null)
    if (mId === null || mId === undefined) {
        alert("错误：无法在项目根目录下直接创建用例，请先创建并点击进入一个模块！");
        return;
    }
    const payload = {
            module_id: parseInt(mId), // 确保是数字类型
            name: document.getElementById('c-name').value,
            description: document.getElementById('c-desc').value,
            skip: document.getElementById('c-skip').checked,
            method: document.getElementById('c-method').value,
            path: document.getElementById('c-path').value,
            headers: document.getElementById('c-headers').value,
            data_type: document.getElementById('c-data-type').value,
            params: document.getElementById('c-params').value,
            file_path: document.getElementById('c-file-path').value,
            extract_data: document.getElementById('c-extract').value,
            sql_query: document.getElementById('c-sql').value,
            assertion: document.getElementById('c-assertion').value,
            wait_time: parseInt(document.getElementById('c-wait-time').value) || 0
        };

    // 如果是插入模式，告诉后端插入位置
    if (window.insertIndex !== null) {
        payload.sort_order = window.insertIndex;
    }

    // 2. 基础校验
    if (!payload.module_id) { alert("错误：无法在根目录下创建用例，请先进入模块"); return; }
    if (!payload.name || !payload.path || !payload.assertion) { alert("请填写必填项：名称、路径、断言"); return; }

    // 3. 发送请求
    const url = window.isEditMode ? `${API_BASE}/test_cases/${window.currentEditId}` : `${API_BASE}/test_cases`;
    const method = window.isEditMode ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            window.insertIndex = null; // 重置插入标记
            closeModal('case-modal');
            window.router.projectDetail(window.currentProjectId, window.currentParentId);
        } else {
            const err = await res.json();
            alert("提交失败: " + JSON.stringify(err.detail));
        }
    } catch (e) {
        alert("网络异常或后端接口未实现");
    }
}

async function executeRun(payload) {
    try {
        const res = await fetch(`${API_BASE}/run_test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'success') {
            alert(`测试已启动：共 ${data.total} 条用例`);
        } else {
            alert(`启动失败：${data.message}`);
        }
    } catch (err) {
        console.error("执行测试出错:", err);
    }
}

function runProjectCases(projectId) {
    executeRun({ project: projectId });
}

function runTestHandler(projectId, moduleId, caseId){
    executeRun({ project: projectId, module: moduleId, case: caseId });
}

// --- 编辑用例 ---
function openCaseModal(caseData = null) {
    const modal = document.getElementById('case-modal');
    if (caseData) {
        // 编辑模式：回填数据 (根据你的TestCaseCreate模型)
        document.getElementById('c-name').value = caseData.name;
        document.getElementById('c-desc').value = caseData.description;
        document.getElementById('c-method').value = caseData.method;
        document.getElementById('c-path').value = caseData.path;
        document.getElementById('c-headers').value = caseData.headers;
        document.getElementById('c-data-type').value = caseData.data_type;
        document.getElementById('c-params').value = caseData.params;
        document.getElementById('c-file-path').value = caseData.file_path;
        document.getElementById('c-extract').value = caseData.extract_data;
        document.getElementById('c-sql').value = caseData.sql_query;
        document.getElementById('c-assertion').value = caseData.assertion;
        document.getElementById('c-wait-time').value = caseData.wait_time;
    } else {
        // 新增模式：清空表单
        document.querySelectorAll('#case-modal input, #case-modal textarea').forEach(el => el.value = '');
    }
    modal.style.display = 'flex';
}

// --- 新增用例 ---
function newCaseModal() {
    window.isEditMode = false;
    window.currentEditId = null;
    window.insertIndex = null; // 普通新增不需要插入位置
    resetCaseModal();
    document.getElementById('case-modal-title').innerText = "新增测试用例";
    document.querySelectorAll('#case-modal input, #case-modal textarea').forEach(el => el.value = '');
    document.getElementById('case-modal').style.display = 'flex';

}

function editCaseModal(item) {
    window.isEditMode = true;
    window.currentEditId = item.id;

    // 使用 .value 赋值是最安全的，会自动处理特殊字符
    document.getElementById('c-name').value = item.name || '';
    document.getElementById('c-path').value = item.path || '';
    document.getElementById('c-params').value = item.params || ''; // 即使有单引号也没事
    document.getElementById('c-sql').value = item.sql_query || item.sql || ''; // 即使有换行也没事

    // 显示弹窗
    document.getElementById('case-modal-title').innerText = "编辑用例";
    document.getElementById('case-modal').style.display = 'flex';
    openCaseModal(item);
}

function formatAndHighlight(text) {
    if (!text) return '<span style="color:#999">空</span>';

    // 1. 尝试 JSON 格式化
    let displayed = text;
    try {
        const obj = JSON.parse(text);
        displayed = JSON.stringify(obj, null, 2);
    } catch (e) {
        // 不是 JSON 则保持原样
    }

    // 2. 转义 HTML 标签防止脚本注入
    displayed = displayed.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // 3. 正则高亮
    // ${variable} -> 蓝色
    displayed = displayed.replace(/\$\{[a-zA-Z0-9_]+\}/g, '<span style="color: #007bff; font-weight: bold;">$&</span>');
    // $.json.path -> 绿色
    displayed = displayed.replace(/\$\.[a-zA-Z0-9_.*\[\]]+/g, '<span style="color: #28a745; font-weight: bold;">$&</span>');
    // function:name -> 橙色
    displayed = displayed.replace(/function:[a-zA-Z0-9_]+/g, '<span style="color: #fd7e14; font-weight: bold;">$&</span>');

    return displayed;
}

async function submitImport() {
    const fileInput = document.getElementById('import-file-input');
    if (!fileInput.files[0]) {
        alert("请选择文件");
        return;
    }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    // 注意：这里的 module_id 必须从全局变量获取，因为导入必须在模块下
    const url = `${API_BASE}/projects/${window.currentProjectId}/import_cases?module_id=${window.currentParentId}`;

    try {
        const res = await fetch(url, {
            method: 'POST',
            body: formData // 注意：使用 FormData 时不要手动设置 Content-Type header
        });

        if (res.ok) {
            const result = await res.json();
            alert(result.message);
            closeModal('import-modal');
            // 刷新列表
            window.router.projectDetail(window.currentProjectId, window.currentParentId);
        } else {
            const err = await res.json();
            alert("导入失败: " + err.detail);
        }
    } catch (e) {
        console.error(e);
        alert("网络错误");
    }
}

function insertCaseAfter(index) {
    window.insertIndex = index + 1; // 在当前行之后插入
    window.isEditMode = false;      // 插入本质是新增
    window.currentEditId = null;

    // 重置并打开新增用例弹窗
    newCaseModal();
    document.getElementById('case-modal-title').innerText = `在第 ${index + 1} 行后插入新用例`;
    document.getElementById('case-modal').style.display = 'flex';
}

function resetCaseModal() {
    // 1. 清空所有输入框
    document.getElementById('c-name').value = '';
    document.getElementById('c-desc').value = '';
    document.getElementById('c-path').value = '';
    document.getElementById('c-headers').value = '';
    document.getElementById('c-params').value = '';
    document.getElementById('c-extract').value = '';
    document.getElementById('c-sql').value = '';
    document.getElementById('c-assertion').value = '';
    document.getElementById('c-wait-time').value = 0;

    // 2. 恢复默认选项
    document.getElementById('c-method').value = 'POST';
    document.getElementById('c-data-type').value = 'application/json';
    document.getElementById('c-skip').checked = false;

    // 3. 隐藏错误提示（如果有 JSON 校验的话）
    const errors = document.querySelectorAll('.error-msg');
    errors.forEach(err => err.style.display = 'none');
}

// --- 拖拽排序逻辑 ---
function initDragging() {
    const list = document.getElementById('sortable-list');
    let draggingEle = null;

    // 1. 开始拖拽
    list.addEventListener('dragstart', (e) => {
        draggingEle = e.target.closest('.draggable');
        if (draggingEle) {
            e.dataTransfer.effectAllowed = 'move';
            draggingEle.classList.add('dragging'); // 可选：添加拖拽中样式
        }
    });

    // 2. 拖拽过程中计算位置（这是实现“实质排序”的关键）
    list.addEventListener('dragover', (e) => {
        e.preventDefault();
        const target = e.target.closest('.draggable');

        // 确保目标是另一行且不是正在拖拽的行
        if (target && target !== draggingEle) {
            const rect = target.getBoundingClientRect();
            // 计算鼠标在目标行上的位置，超过一半则移到目标行后面
            const next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;

            // 移动主行
            const nodeToInsert = next ? target.nextSibling : target;
            list.insertBefore(draggingEle, nodeToInsert);

            // 【重要】联动移动预览行：确保预览详情始终紧跟在主行下方
            const previewRow = document.getElementById(`preview-row-${draggingEle.dataset.id}`);
            if (previewRow) {
                list.insertBefore(previewRow, draggingEle.nextSibling);
            }
        }
    });

    // 3. 拖拽结束，保存数据并更新序号
    list.addEventListener('dragend', async () => {
        if (draggingEle) draggingEle.classList.remove('dragging');

        // 只获取带 ID 的主行进行排序上报
        const rows = Array.from(list.querySelectorAll('tr.draggable'));
        const updateData = rows.map((row, index) => ({
            id: parseInt(row.dataset.id),
            type: row.dataset.type,
            new_order: index
        }));

        const cleanData = updateData.filter(item => !isNaN(item.id));

        // 发送后端请求
        try {
            const res = await fetch(`${API_BASE}/reorder`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(cleanData)
            });

            if (res.ok) {
                // 【实时更新序号】
                const currentRows = list.querySelectorAll('tr.draggable');
                currentRows.forEach((row, index) => {
                    const orderCell = row.cells[1]; // 假设序号在第2列
                    if (orderCell) {
                        orderCell.innerText = index + 1;
                    }
                });
                console.log("排序保存成功");
            }
        } catch (error) {
            console.error("排序保存失败:", error);
        }

        draggingEle = null;
    });
}

// --- JSON 校验 ---
function validateJson(el) {
    const errorSpan = el.nextElementSibling;
    if (!el.value.trim()) {
        errorSpan.style.display = 'none';
        return true;
    }
    try {
        JSON.parse(el.value);
        errorSpan.style.display = 'none';
        el.style.border = "1px solid #ccc";
        return true;
    } catch (e) {
        errorSpan.style.display = 'block';
        el.style.border = "1px solid red";
        return false;
    }
}

// --- 全选逻辑 ---
function toggleAll(master) {
    const checkboxes = document.querySelectorAll('.item-checkbox');
    checkboxes.forEach(cb => cb.checked = master.checked);
}

window.router = router;

// 初始化
window.onload = () => {
    window.router.home();
};
