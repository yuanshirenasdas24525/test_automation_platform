// script.js
const API_BASE = "http://127.0.0.1:54351/api";
// 统一使用 window 对象挂载，确保全局唯一性
window.currentProjectId = null;
window.currentParentId = null;
window.isEditMode = false;
window.currentEditId = null;
window.currentListData = [];
window.insertIndex = null;
window.viewProjectDetails = (id, mid, category) => router.projectDetail(id, mid, category);
// --- 路由与视图控制 ---
const router = {
    // 1. 首页：展示所有项目
    home: function() {
        const container = document.getElementById('view-container');

        container.innerHTML = `
            <div class="home-container" style="padding: 30px;">
                <div class="project-intro-card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 16px; margin-bottom: 30px; box-shadow: 0 10px 20px rgba(0,0,0,0.1);">
                    <h1 style="margin: 0; font-size: 28px;">一站式质量保障中心</h1>
                    <p style="margin-top: 15px; opacity: 0.9; line-height: 1.6; max-width: 800px;">
                        本平台旨在解决测试过程中的复杂业务逻辑验证痛点。
                        集成 <b>API、Web、Mobile</b> 三端自动化能力，支持热加载配置中心，
                        实现从需求分析到测试报告的全链路闭环。
                    </p>
                    <div style="margin-top: 20px; display: flex; gap: 20px;">
                        <div class="badge">当前版本: v2.0.4</div>
                        <div class="badge">运行环境: macOS / Python 3.9</div>
                    </div>
                </div>
    
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px;">
                    ${renderStatCard('项目总数', '12', 'fa-folder', '#3b82f6')}
                    ${renderStatCard('API用例', '856', 'fa-api', '#10b981')}
                    ${renderStatCard('Web项目', '4', 'fa-chrome', '#f59e0b')}
                    ${renderStatCard('执行成功率', '98.5%', 'fa-check-circle', '#ef4444')}
                </div>
    
                <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 30px;">
                    <div class="card" style="background:white; padding:20px; border-radius:12px; border:1px solid #eee;">
                        <h3 style="margin-bottom:15px; display:flex; align-items:center;"><i class="fas fa-history" style="margin-right:10px; color:#6366f1;"></i> 最近动态</h3>
                        <ul style="list-style:none; padding:0; font-size:14px; color:#475569;">
                            <li style="padding:10px 0; border-bottom:1px solid #f1f5f9;">🚀 API 自动化配置已更新 [category: api]</li>
                            <li style="padding:10px 0; border-bottom:1px solid #f1f5f9;">✅ 登录模块测试用例执行完成</li>
                            <li style="padding:10px 0;">🔧 用户新增了 Web 自动化配置组</li>
                        </ul>
                    </div>
                    <div class="card" style="background:white; padding:20px; border-radius:12px; border:1px solid #eee;">
                        <h3 style="margin-bottom:15px;">系统负载</h3>
                        <div style="margin-bottom:15px;">
                            <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:5px;"><span>数据库连接 (SQLite)</span><span>正常</span></div>
                            <div style="height:6px; background:#f1f5f9; border-radius:3px;"><div style="width:20%; height:100%; background:#10b981; border-radius:3px;"></div></div>
                        </div>
                        <div style="margin-bottom:15px;">
                            <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:5px;"><span>热加载内存占用</span><span>24MB</span></div>
                            <div style="height:6px; background:#f1f5f9; border-radius:3px;"><div style="width:45%; height:100%; background:#3b82f6; border-radius:3px;"></div></div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    // 2. 项目管理：按类型（API/Web/Mobile）过滤
    projectList: async function(category) {
        const container = document.getElementById('view-container');
        container.innerHTML = `<div style="padding:40px; text-align:center; color:#94a3b8;"><i class="fas fa-spinner fa-spin"></i> 正在调取 ${category} 用例库...</div>`;

        try {
            // 假设接口支持按类型获取项目列表
            const response = await fetch(`/api/projects/list?type=${category}`);
            const result = await response.json();

            let html = `
                <div style="padding: 24px 30px; display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h2 style="margin:0; font-size:22px; color:#1e293b;">${category} 自动化用例库</h2>
                        <p style="margin:5px 0 0 0; font-size:13px; color:#64748b;">管理、执行并监控您的测试套件</p>
                    </div>
                    <button class="btn btn-primary" onclick="newProjectModal('${category}')">
                        <i class="fas fa-plus"></i> 创建新项目
                    </button>
                </div>
                <div class="project-grid">
            `;

            // 循环渲染卡片
            result.data.forEach(proj => {
                const caseCount = proj.case_count || 0;
                const passRate = proj.pass_rate || 0;
                const lastRun = proj.last_run_time || '从未执行';
                const statusColor = proj.last_status === 'success' ? '#10b981' : (proj.last_status === 'fail' ? '#ef4444' : '#cbd5e1');
                const icon = category === 'API' ? 'fa-bolt' : (category === 'Web' ? 'fa-desktop' : 'fa-mobile-alt');

                html += `
                    <div class="project-card">
                        <div class="project-status-bar" style="background: ${statusColor}"></div>
                        <div class="project-header">
                            <div class="project-icon-box" style="background: ${statusColor}15; color: ${statusColor}">
                                <i class="fas ${icon}"></i>
                            </div>
                            <div class="dropdown" onclick="toggleProjectMenu(event, this)">
                                <i class="fas fa-ellipsis-v" style="color:#cbd5e1; cursor:pointer; padding:5px;"></i>
                                <ul class="dropdown-menu">
                                    <li onclick="editProject(${proj.id})"><i class="fas fa-edit"></i> 编辑项目</li>
                                    <li class="text-danger" onclick="deleteProject(${proj.id})">
                                        <i class="fas fa-trash"></i> 删除项目
                                    </li>
                                </ul>
                            </div>
                        </div>
                        
                        <h3 style="margin: 0 0 8px 0; font-size: 16px; color: #1e293b;">${proj.name}</h3>
                        <p style="font-size: 12px; color: #64748b; line-height: 1.5; height: 36px; overflow: hidden;">
                            ${proj.desc || '暂无项目描述，点击设置添加内容'}
                        </p>
    
                        <div class="project-stats">
                            <div class="stat-item">
                                <div class="stat-label">用例总数</div>
                                <div class="stat-value">${caseCount}</div>
                            </div>
                            <div class="stat-item" style="border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0;">
                                <div class="stat-label">通过率</div>
                                <div class="stat-value" style="color: ${statusColor}">${passRate}%</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-label">上次执行</div>
                                <div class="stat-value" style="font-size:11px;">${lastRun}</div>
                            </div>
                        </div>
    
                        <div class="project-actions">
                            <button class="action-btn-mini btn-run" onclick="runProjectCases(${proj.id},  '${category}')">
                                <i class="fas fa-play"></i> 执行
                            </button>
                            <button class="action-btn-mini" onclick="viewProjectDetails(${proj.id}, null, '${category}')">
                                <i class="fas fa-eye"></i> 详情
                            </button>
                            <button class="action-btn-mini" onclick="editProject(${proj.id})">
                                <i class="fas fa-edit"></i> 编辑
                            </button>
                        </div>
                    </div>
                `;
            });

            html += `</div>`;
            container.innerHTML = html;
        } catch (err) {
            container.innerHTML = `<div style="padding:100px; text-align:center; color:#ef4444;">加载失败，请检查后端服务</div>`;
        }
    },

    // 3. 进入项目/模块详情
    async projectDetail(projId, moduleId = null, category) {
        // 严谨的参数处理
        if (!moduleId || moduleId === "null" || moduleId === "undefined") moduleId = null;

        window.currentProjectId = projId;
        window.currentParentId = moduleId;

        try {
            let parentIdForBack = null;
            let displayName = "";

            // 1. 获取面包屑名称逻辑
            if (moduleId) {
                const modRes = await fetch(`/api/modules/${moduleId}`);
                if (modRes.ok) {
                    const modData = await modRes.json();
                    parentIdForBack = modData.parent_id;
                    displayName = `📂 ${modData.name}`;
                }
            } else {
                const projRes = await fetch(`/api/projects/${projId}`);
                if (projRes.ok) {
                    const projData = await projRes.json();
                    displayName = `🚀 ${projData.name}`;
                }
            }

            // 2. 获取列表数据
            let contentUrl = `/api/content/${projId}`;
            if (moduleId) contentUrl += `?parent_id=${moduleId}`;
            const res = await fetch(contentUrl);
            const data = await res.json();
            window.currentListData = data;

            // 3. 构建沉浸式 UI
            let html = `
                <div class="detail-header" style="padding: 24px 30px; background: white; border-bottom: 1px solid #e2e8f0;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div class="breadcrumb-nav" style="display: flex; align-items: center; gap: 8px;">
                            <button class="btn-icon" onclick="router.home()"><i class="fas fa-home"></i></button>
                            <i class="fas fa-chevron-right" style="font-size: 10px; color: #cbd5e1;"></i>
                            ${moduleId ? 
                                `<button class="btn-text" onclick="router.projectDetail(${projId}, ${parentIdForBack}, '${category}')">返回上级</button>
                                 <i class="fas fa-chevron-right" style="font-size: 10px; color: #cbd5e1;"></i>` : ''
                            }
                            <span style="font-weight: 600; color: #1e293b; font-size: 18px;">${displayName}</span>
                        </div>
                        <div style="display: flex; gap: 10px;">
                            <button class="btn btn-outline" onclick="newModuleModal()"><i class="fas fa-folder-plus"></i> 新增模块</button>
                            ${moduleId !== null ? `
                                <button class="btn btn-outline" onclick="document.getElementById('import-modal').style.display='flex'"><i class="fas fa-file-import"></i> 导入</button>
                                <button class="btn btn-primary" onclick="newCaseModal()"><i class="fas fa-plus"></i> 新增用例</button>
                            ` : ''}
                        </div>
                    </div>
                </div>
    
                <div style="padding: 20px 30px;">
                    <table class="modern-table">
                        <thead>
                            <tr>
                                <th width="40"><input type="checkbox" id="selectAll"></th>
                                <th width="60">#</th>
                                <th>名称 / 路径</th>
                                <th width="100">方法</th>
                                <th width="200">操作</th>
                            </tr>
                        </thead>
                        <tbody id="sortable-list">
            `;

            if (!data || data.length === 0) {
                html += `<tr><td colspan="5" style="text-align:center; padding:60px; color:#94a3b8;">
                    <i class="fas fa-inbox" style="font-size:48px; display:block; margin-bottom:10px;"></i>
                    暂无内容，点击上方按钮开始添加
                </td></tr>`;
            } else {
                data.forEach((item, index) => {
                    const isMod = item.type === 'module';
                    const clickAction = isMod ? `router.projectDetail(${projId}, ${item.id}, '${category}')` : `toggleCasePreview(${item.id})`;
                    const icon = isMod ? 'fa-folder' : 'fa-file-alt';
                    const iconColor = isMod ? '#3b82f6' : '#94a3b8';
                    const belongsToModuleId = isMod ? item.id : (item.module_id || window.currentParentId);

                    html += `
                        <tr class="draggable-row" data-id="${item.id}" data-type="${item.type}">
                            <td><input type="checkbox" class="item-checkbox" value="${item.id}"></td>
                            <td style="color:#cbd5e1; font-family:monospace;">${(index + 1).toString().padStart(2, '0')}</td>
                            <td>
                                <div onclick="${clickAction}" style="cursor:pointer; display:flex; align-items:center; gap:10px;">
                                    <i class="fas ${icon}" style="color:${iconColor}; font-size:16px;"></i>
                                    <div>
                                        <div style="font-weight: ${isMod ? '600' : '400'}; color:#1e293b;">${item.name}</div>
                                        ${!isMod ? `<div style="font-size:11px; color:#94a3b8; font-family:monospace;">${item.path || '/'}</div>` : ''}
                                    </div>
                                </div>
                            </td>
                            <td>
                                ${!isMod ? `<span class="method-badge method-${item.method?.toLowerCase() || 'get'}">${item.method || 'GET'}</span>` : '<span style="color:#cbd5e1">—</span>'}
                            </td>
                            <td>
                                <div style="display:flex; gap:8px;">
                                    <button class="btn-circle btn-run" title="运行" onclick="runTestHandler(${projId}, ${belongsToModuleId}, ${isMod ? 'null' : item.id}, '${category}')"><i class="fas fa-play"></i></button>
                                    <button class="btn-circle btn-edit" title="编辑" onclick="${isMod ? `editModule(${item.id})` : `handleEditCase(${item.id})`}"><i class="fas fa-edit"></i></button>
                                    <button class="btn-circle btn-delete" title="删除" onclick="confirmDelete('${item.type}', ${item.id})"><i class="fas fa-trash"></i></button>
                                </div>
                            </td>
                        </tr>
                        ${!isMod ? `
                        <tr id="preview-row-${item.id}" style="display:none; background: #fcfcfd;">
                            <td colspan="5">
                                <div class="case-preview-card" style="margin: 10px 20px; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; background: white;">
                                    
                                    <div style="margin-bottom: 15px; display: flex; gap: 20px; border-bottom: 1px solid #f1f5f9; padding-bottom: 10px;">
                                        <div style="flex: 1;">
                                            <span style="color: #64748b; font-size: 12px; display: block;">请求完整路径</span>
                                            <code style="color: #1e293b; font-family: monospace;">${highlightSpecialSyntax(item.path || '/')}</code>
                                        </div>
                                        ${item.description ? `
                                        <div style="flex: 1;">
                                            <span style="color: #64748b; font-size: 12px; display: block;">用例描述</span>
                                            <span style="color: #475569;">${item.description}</span>
                                        </div>` : ''}
                                    </div>
                        
                                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                                        
                                        ${item.headers ? `
                                        <div class="preview-box">
                                            <label>Headers / Content-Type</label>
                                            <pre>${formatAndHighlight(item.headers)}</pre>
                                        </div>` : ''}
                        
                                        ${item.params ? `
                                        <div class="preview-box">
                                            <label>请求参数 (Payload)</label>
                                            <pre>${formatAndHighlight(item.params)}</pre>
                                        </div>` : ''}
                        
                                        ${item.extract_data ? `
                                        <div class="preview-box">
                                            <label>数据提取 (Extract)</label>
                                            <pre>${formatAndHighlight(item.extract_data)}</pre>
                                        </div>` : ''}
                        
                                        ${item.assertion ? `
                                        <div class="preview-box">
                                            <label>断言校验 (Assertion)</label>
                                            <pre>${formatAndHighlight(item.assertion)}</pre>
                                        </div>` : ''}
                        
                                        ${item.sql_query ? `
                                        <div class="preview-box">
                                            <label>预置 SQL 语句</label>
                                            <pre style="color: #059669;">${highlightSpecialSyntax(item.sql_query)}</pre>
                                        </div>` : ''}
                        
                                        ${item.file_path ? `
                                        <div class="preview-box">
                                            <label>上传文件路径</label>
                                            <div style="padding: 8px; background: #fff1f2; color: #e11d48; border-radius: 4px; font-size: 12px; font-family: monospace;">
                                                <i class="fas fa-paperclip"></i> ${item.file_path}
                                            </div>
                                        </div>` : ''}
                                    </div>
                        
                                    <div style="margin-top: 15px; padding-top: 10px; border-top: 1px solid #f1f5f9; display: flex; gap: 20px; font-size: 12px; color: #94a3b8;">
                                        <span>等待时间: <b style="color: #1e293b;">${item.wait_time || 0}s</b></span>
                                        <span>创建人: <b style="color: #1e293b;">${item.creator || 'Admin'}</b></span>
                                    </div>
                                </div>
                            </td>
                        </tr>` : ''}
                    `;
                });
            }

            html += `</tbody></table></div>`;
            document.getElementById('view-container').innerHTML = html;
            if (window.initDragging) initDragging();

        } catch (err) {
            console.error("加载详情失败:", err);
        }
    },

    configManager: async function(type = null) {
        console.log("路由跳转：配置管理");
        const container = document.getElementById('view-container');

        let url = '/api/config/all';
        if (type) {
            url += `?category=${type}`;
        }

        try {
            // 调用你之前写好的获取所有配置的接口
            const response = await fetch(url);
            const result = await response.json();

            if (result.status === 'success') {
            // 传入 type 以便在 UI 上显示当前分类标题
                this.renderConfigView(result.data, type);
            }
        } catch (err) {
            container.innerHTML = '<div class="error">网络错误，无法连接到后端配置接口</div>';
            console.error(err);
        }
    },

    renderConfigView: function(data, type) {
        const container = document.getElementById('view-container');

        // 按 config_group 分组逻辑
        const groups = data.reduce((acc, item) => {
                (acc[item.config_group] = acc[item.config_group] || []).push(item);
                return acc;
        }, {});



        let html = `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:20px 30px;">
                <div>
                    <h2 style="margin:0; color:#1e293b;">${type ? type.toUpperCase() : '全部'} 配置中心</h2>
                    <p style="margin:5px 0 0 0; font-size:13px; color:#64748b;">分类管理自动化环境参数</p>
                </div>
                <div style="display:flex; gap:12px;">
                    <button class="btn" onclick="showAddGroupModal('${type}')" style="background:#f8fafc; border:1px solid #e2e8f0; color:#475569;">
                        <i class="fas fa-folder-plus"></i> 新增配置组
                    </button>
                    <button class="btn btn-primary" onclick="submitGlobalConfigs()">
                        <i class="fas fa-save"></i> 保存全部修改
                    </button>
                </div>
            </div>
            <div class="config-container">
        `;

        Object.keys(groups).forEach(groupName => {
            html += `
                <div class="config-card">
                    <div class="card-header">
                        <span class="card-title"><i class="fas fa-server" style="color:#4299e1; margin-right:8px;"></i>${groupName}</span>
                        <button onclick="addInlineRow('${groupName}','${type}')" style="background:#edf2f7; border:none; padding:5px 12px; border-radius:4px; cursor:pointer; font-size:12px; font-weight:bold; color:#4a5568;">
                            <i class="fas fa-plus"></i> 新增项
                        </button>
                    </div>
                    <div class="card-body" id="group-content-${groupName}">
                        ${groups[groupName].map(item => `
                            <div class="config-row" id="config-item-${item.id}">
                                <div class="config-info">
                                    <div class="config-key-label">${item.config_key}</div>
                                    <input type="${(item.config_key.includes('pass') || item.config_key.includes('key')) ? 'password' : 'text'}" 
                                           class="config-input config-data-field"
                                           data-id="${item.id}" data-group="${item.config_group}" data-key="${item.config_key}"
                                           value="${item.config_value}">
                                </div>
                                <div class="delete-btn" onclick="deleteHistoryConfig(${item.id})" title="永久删除">
                                    <i class="fas fa-trash-alt"></i>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        container.innerHTML = html;
    },

    // 在 router 对象中增加工具模块处理函数
    toolModule: function(type) {
        const container = document.getElementById('view-container');
        let title = "";
        let description = "";

        switch(type) {
            case 'requirement':
                title = "需求分析助手";
                description = "基于自然语言处理，自动提取测试点及业务逻辑";
                break;
            case 'generator':
                title = "智能用例生成";
                description = "根据接口定义或 UI 路径，自动填充测试步骤与断言";
                break;
            case 'analysis':
                title = "用例覆盖度分析";
                description = "分析当前自动化用例对业务场景的覆盖比例";
                break;
        }

        container.innerHTML = `
            <div style="padding: 30px;">
                <div style="border-bottom: 1px solid #eee; padding-bottom: 20px; margin-bottom: 20px;">
                    <h2 style="color: #1e293b; margin: 0;">${title}</h2>
                    <p style="color: #64748b; margin-top: 5px;">${description}</p>
                </div>
                <div class="tool-content-placeholder" style="height: 400px; display: flex; align-items: center; justify-content: center; background: #f8fafc; border: 2px dashed #e2e8f0; border-radius: 12px; color: #94a3b8;">
                    <div style="text-align: center;">
                        <i class="fas fa-tools" style="font-size: 40px; margin-bottom: 15px;"></i>
                        <p>该功能正在接入 AI 引擎，请稍候...</p>
                    </div>
                </div>
            </div>
        `;

        // 记录日志，方便排查用户点击偏好
        console.log(`[Router] 进入测试工具子模块: ${type}`);
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

// --- 新增项目 ---
function newProjectModal() {
    window.isEditMode = false;
    window.currentEditId = null;

    // 清空表单
    document.getElementById('p-name').value = '';
    document.getElementById('p-desc').value = '';
    document.getElementById('modal-title').innerText = "新增项目";

    document.getElementById('project-modal').style.display = 'flex';
}

// --- 编辑项目 ---
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

// --- 删除项目 ---
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

// --- 新增模块 ---
function newModuleModal() {
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

function runProjectCases(projectId, category) {
    executeRun({ project: projectId ,  category: category});
}

function runTestHandler(projectId, moduleId, caseId, category){
    executeRun({ project: projectId, module: moduleId, case: caseId , category: category});
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


// 格式化文本并高亮
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

// 处理点击编辑按钮的逻辑
function handleEditCase(id) {
    // 从刚才保存的全局变量中找到对应的 item 对象
    const item = window.currentListData.find(i => i.id === id);

    if (item) {
        editCaseModal(item);
    } else {
        console.error("未找到该用例数据，ID:", id);
    }
}

function toggleCasePreview(id) {
    const previewRow = document.getElementById(`preview-row-${id}`);
    if (previewRow) {
        // 切换显示状态
        const isHidden = previewRow.style.display === 'none';
        previewRow.style.display = isHidden ? 'table-row' : 'none';

        // 给主行添加高亮样式
        const mainRow = previewRow.previousElementSibling;
        if (isHidden) {
            mainRow.style.backgroundColor = "#f0f7ff";
        } else {
            mainRow.style.backgroundColor = "";
        }
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


// config全局保存函数
window.submitGlobalConfigs = async function() {
    const inputs = document.querySelectorAll('.config-input-field');
    const configs = Array.from(inputs).map(el => ({
        config_group: el.dataset.group,
        config_key: el.dataset.key,
        config_value: el.value
    }));

    try {
        const response = await fetch('/api/config/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configs)
        });
        const res = await response.json();
        if(res.status === 'success') {
            alert("✅ 配置已存入 SQLite 并完成内存热重载！");
        }
    } catch (e) {
        alert("保存失败，请检查后端 API 状态");
    }
};

// --- config删除逻辑 ---
window.deleteConfig = async function(id) {
    if (!confirm("确定要删除该配置吗？这将影响相关自动化用例的执行。")) return;

    const res = await fetch(`/api/config/delete/${id}`, { method: 'DELETE' });
    const result = await res.json();
    if (result.status === 'success') {
        router.configManager(); // 刷新页面
    }
};

// --- config新增逻辑 (复用你现有的 Modal 体系) ---
window.showAddConfigModal = function() {
    // 你可以复用 index.html 里的 project-modal 结构，或者新写一个简单的 prompt
    const group = prompt("请输入配置组 (如: mysql_db):");
    const key = prompt("请输入配置键 (如: port):");
    const val = prompt("请输入配置值:");

    if (group && key && val) {
        saveNewConfig(group, key, val);
    }
};

// --- 内联新增一行 (不需要弹出框，直接在组内画输入框) ---
window.addNewRowInline = function(groupName) {
    const body = document.getElementById(`group-body-${groupName}`);
    const div = document.createElement('div');
    div.className = "config-row animate-pulse"; // 加入动画效果
    div.style.background = "#fffbeb"; // 临时高亮新行

    div.innerHTML = `
        <div style="flex:1;">
            <input type="text" placeholder="新 Key" class="new-key-input input-minimal" style="color:#b45309; font-weight:bold;">
            <input type="text" placeholder="新 Value" class="new-val-input input-minimal">
        </div>
        <button onclick="saveNewInline(this, '${groupName}')" style="color:#059669;"><i class="fas fa-check-circle"></i></button>
        <button onclick="this.parentElement.remove()" style="color:#94a3b8;"><i class="fas fa-minus-circle"></i></button>
    `;
    body.prepend(div); // 在最前面插入新项
};

window.saveNewInline = async function(btn, groupName) {
    const row = btn.parentElement;
    const key = row.querySelector('.new-key-input').value;
    const val = row.querySelector('.new-val-input').value;

    if(!key || !val) return alert("Key 和 Value 不能为空");

    const category = window.currentCategory || 'api';

    const res = await fetch('/api/config/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_group: groupName, config_key: key, config_value: val, category: category })
    });

    const result = await res.json();
    if(result.status === 'success') {
        // 重新加载当前分类的视图，防止跳回全量视图
        router.configManager(category);
    } else {
        alert("保存失败: " + (result.message || "未知错误"));
    }
};

async function saveNewConfig(group, key, val) {
    const res = await fetch('/api/config/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_group: group, config_key: key, config_value: val })
    });
    const result = await res.json();
    if (result.status === 'success') {
        router.configManager();
    }
}

// --- 功能1：内联新增（带取消功能） ---
// --- 功能：内联新增（无需刷新即可撤销） ---
// --- 1. 点击“新增项”按钮触发的函数 ---
window.addInlineRow = function(groupName, type) {
    const body = document.getElementById(`group-content-${groupName}`);
    const tempId = 'temp-' + Date.now();
    const div = document.createElement('div');

    // 设置样式类名，方便在 CSS 中统一定义样式
    div.className = "new-row-highlight";
    div.id = tempId;

    div.innerHTML = `
        <div style="display: flex; gap: 10px; align-items: center; padding: 12px; background: #fffbeb; border: 1px dashed #f6ad55; border-radius: 10px; margin-bottom: 10px;">
            <div style="flex: 1;">
                <input type="text" placeholder="Key (必填)" class="new-key-in" style="width:100%; border:none; border-bottom:1px solid #f6ad55; background:transparent; font-weight:bold; outline:none; margin-bottom:8px;">
                <input type="text" placeholder="Value (必填)" class="new-val-in" style="width:100%; border:none; background:transparent; outline:none; font-size:13px;">
            </div>
            
            <div style="display: flex; flex-direction: column; gap: 8px;">
                <button onclick="confirmAndSaveRow('${tempId}', '${groupName}', '${type}')" 
                        style="background: #38a169; color: white; border: none; border-radius: 4px; padding: 4px 8px; cursor: pointer; font-size: 12px;">
                    确认
                </button>
                <button onclick="document.getElementById('${tempId}').remove()" 
                        style="background: #e53e3e; color: white; border: none; border-radius: 4px; padding: 4px 8px; cursor: pointer; font-size: 12px;">
                    取消
                </button>
            </div>
        </div>
    `;
    body.prepend(div);
};

// --- 2. 核心：执行“确认”并提交后端 ---
window.confirmAndSaveRow = async function(tempId, groupName, type) {
    const row = document.getElementById(tempId);
    const key = row.querySelector('.new-key-in').value.trim();
    const val = row.querySelector('.new-val-in').value.trim();

    if (!key || !val) {
        alert("Key 和 Value 都不能为空！");
        return;
    }

    // 调用后端接口保存到数据库
    try {
        const response = await fetch('/api/config/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                config_group: groupName,
                config_key: key,
                config_value: val,
                category: type
            })
        });

        const res = await response.json();
        if (res.status === 'success') {
            // 刷新页面看到新数据
            router.configManager();
            console.log(`已成功添加配置：${key}`);
        } else {
            alert("保存失败：" + (res.message || "未知错误"));
        }
    } catch (e) {
        alert("网络错误，提交失败");
    }
};

// --- 功能：删除历史数据 ---
window.deleteHistoryConfig = async function(id) {
    if (!confirm("警告：确定要从数据库中永久删除此项吗？")) return;

    try {
        const response = await fetch(`/api/config/delete/${id}`, { method: 'DELETE' });
        const res = await response.json();
        if (res.status === 'success') {
            const row = document.getElementById(`config-item-${id}`);
            row.style.transform = "translateX(20px)";
            row.style.opacity = "0";
            setTimeout(() => {
                router.configManager(); // 刷新视图
                console.log(`[Config] 已删除配置项ID: ${id}`);
            }, 300);
        }
    } catch (e) {
        alert("删除失败，请检查后端 API");
    }
};

// --- 功能3：保存新增 ---
window.saveNewRow = async function(rowId, groupName) {
    const row = document.getElementById(rowId);
    const key = row.querySelector('.new-key-in').value;
    const val = row.querySelector('.new-val-in').value;

    if(!key || !val) return alert("请填写完整");

    const res = await fetch('/api/config/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_group: groupName, config_key: key, config_value: val })
    });

    if((await res.json()).status === 'success') {
        router.configManager();
    }
};

// 弹出新增组的对话框
window.showAddGroupModal = async function(currentType) {
    const groupName = prompt("请输入新配置组名称 (例如: web_server):");
    if (!groupName) return;

    const firstKey = prompt(`在 [${groupName}] 中创建第一个 Key:`);
    const firstValue = prompt(`请输入 [${firstKey}] 的初始值:`);

    if (groupName && firstKey && firstValue) {
        // 调用你之前写好的 add 接口
        try {
            const response = await fetch('/api/config/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    config_group: groupName,
                    config_key: firstKey,
                    config_value: firstValue,
                    category: currentType || 'api' // 自动带入当前页面的分类
                })
            });

            const res = await response.json();
            if (res.status === 'success') {
                router.configManager(currentType); // 刷新当前分类页面
                console.log(`[ConfigCenter] 成功创建新组: ${groupName}`);
            }
        } catch (e) {
            alert("创建失败，请检查网络");
        }
    }
};

// 辅助函数：渲染统计卡片
function renderStatCard(title, value, icon, color) {
    return `
        <div style="background: white; padding: 20px; border-radius: 12px; border: 1px solid #eee; display: flex; align-items: center; gap: 15px;">
            <div style="width: 45px; height: 45px; border-radius: 10px; background: ${color}20; color: ${color}; display: flex; align-items: center; justify-content: center; font-size: 20px;">
                <i class="fas ${icon}"></i>
            </div>
            <div>
                <div style="font-size: 13px; color: #64748b;">${title}</div>
                <div style="font-size: 22px; font-weight: 700; color: #1e293b;">${value}</div>
            </div>
        </div>
    `;
}

// 字符串高亮工具函数
function highlightSpecialSyntax(text) {
    if (!text || typeof text !== 'string') return text;

    return text
        .replace(/(\${[^}]+})/g, '<span style="color:#3b82f6; font-weight:bold;">$1</span>') // 变量
        .replace(/(\$\.[a-zA-Z0-9._[\]]+)/g, '<span style="color:#f59e0b; font-weight:bold;">$1</span>') // JSONPath
        .replace(/(function:[a-zA-Z0-9_]+)/g, '<span style="color:#8b5cf6; font-weight:bold;">$1</span>'); // 自定义函数
}

// 切换三个点菜单的显示
window.toggleProjectMenu = function(event, element) {
    event.stopPropagation(); // 阻止事件冒泡，防止触发卡片本身的点击

    // 先关闭其他已经打开的菜单
    document.querySelectorAll('.dropdown').forEach(el => {
        if (el !== element) el.classList.remove('active');
    });

    element.classList.toggle('active');
};

// 点击页面其他地方关闭菜单
document.addEventListener('click', () => {
    document.querySelectorAll('.dropdown').forEach(el => el.classList.remove('active'));
});

// --- 全选逻辑 ---

window.router = router;

// 初始化
window.onload = () => {
    window.router.home();
};
