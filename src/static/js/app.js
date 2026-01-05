/**
 * 动火离人安全监测系统 - Web管理界面
 */

const App = {
    // API基础URL
    apiBase: '',

    // WebSocket连接
    ws: null,
    wsReconnectTimer: null,

    // 当前页面
    currentPage: 'dashboard',

    // 数据缓存
    cameras: [],
    zones: [],
    statuses: [],

    // ROI编辑状态
    roiPoints: [],
    roiZoneId: null,
    roiCameraId: null,

    // 全局配置缓存
    globalSettings: {
        warningTimeout: 30,
        cutoffTimeout: 60
    },

    /**
     * 初始化应用
     */
    init() {
        // 加载全局设置
        this.loadGlobalSettingsFromStorage();

        // 绑定路由
        window.addEventListener('hashchange', () => this.handleRoute());

        // 初始加载
        this.handleRoute();

        // 连接WebSocket
        this.connectWebSocket();

        // 定期刷新状态
        setInterval(() => this.refreshStatus(), 2000);

        // 定期刷新性能数据
        setInterval(() => this.refreshPerformance(), 3000);
        this.refreshPerformance();  // 立即刷新一次
    },

    /**
     * 从localStorage加载全局设置
     */
    loadGlobalSettingsFromStorage() {
        const saved = localStorage.getItem('globalSettings');
        if (saved) {
            try {
                this.globalSettings = JSON.parse(saved);
            } catch (e) {
                console.error('解析保存的设置失败', e);
            }
        }
    },

    /**
     * 路由处理
     */
    handleRoute() {
        const hash = window.location.hash || '#/';
        const page = hash.replace('#/', '') || 'dashboard';
        this.navigateTo(page);
    },

    /**
     * 导航到页面
     */
    navigateTo(page) {
        this.currentPage = page;

        // 更新导航状态
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.page === page);
        });

        // 加载页面内容
        this.loadPage(page);
    },

    /**
     * 加载页面
     */
    async loadPage(page) {
        const main = document.getElementById('main-content');
        const template = document.getElementById(`tpl-${page}`);

        if (!template) {
            main.innerHTML = '<div class="empty-state"><p>页面不存在</p></div>';
            return;
        }

        main.innerHTML = template.innerHTML;

        // 页面初始化
        switch (page) {
            case 'dashboard':
                await this.initDashboard();
                break;
            case 'cameras':
                await this.initCameras();
                break;
            case 'zones':
                await this.initZones();
                break;
            case 'logs':
                await this.initLogs();
                break;
            case 'settings':
                await this.initSettings();
                break;
        }
    },

    // ==================== 仪表盘 ====================

    async initDashboard() {
        await this.loadDeviceInfo();
        await this.refreshStatus();
    },

    async loadDeviceInfo() {
        try {
            const info = await this.api('/device');
            const el = document.getElementById('device-info');
            if (el) {
                el.textContent = `v${info.version}`;
            }
        } catch (e) {
            console.error('加载设备信息失败', e);
        }
    },

    async refreshStatus() {
        try {
            this.statuses = await this.api('/status');
            if (this.currentPage === 'dashboard') {
                this.renderZoneCards();
            }
        } catch (e) {
            console.error('刷新状态失败', e);
        }
    },

    renderZoneCards() {
        const container = document.getElementById('zone-cards');
        if (!container) return;

        // 过滤掉禁用的灶台
        const enabledZones = this.statuses.filter(zone => zone.enabled !== false);

        if (enabledZones.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🍳</div>
                    <p class="empty-state-text">暂无启用的灶台</p>
                </div>
            `;
            return;
        }

        container.innerHTML = enabledZones.map(zone => this.renderZoneCard(zone)).join('');
    },

    renderZoneCard(zone) {
        const statusText = {
            'idle': '空闲',
            'active_with_person': '有人看管',
            'active_no_person': '无人看管',
            'warning': '⚠️ 预警中',
            'cutoff': '🔴 已切电'
        };

        const statusClass = zone.state;
        const showCountdown = zone.state === 'active_no_person' || zone.state === 'warning';
        const countdownValue = zone.state === 'warning' ? zone.cutoff_remaining : zone.warning_remaining;
        const countdownDanger = zone.state === 'warning';

        return `
            <div class="zone-card">
                <div class="zone-card-header">
                    <span class="zone-name">${zone.name}</span>
                    <span class="zone-status ${statusClass}">${statusText[zone.state] || zone.state}</span>
                </div>
                
                ${showCountdown ? `
                    <div class="countdown ${countdownDanger ? 'danger' : ''}">
                        ${Math.ceil(countdownValue)}秒
                    </div>
                ` : ''}
                
                <div class="zone-card-body">
                    <div class="zone-info-item">
                        <span class="zone-info-label">开火状态</span>
                        <span class="zone-info-value">${zone.is_fire_on ? '🔥 开启' : '⚪ 关闭'}</span>
                    </div>
                    <div class="zone-info-item">
                        <span class="zone-info-label">人员状态</span>
                        <span class="zone-info-value">${zone.has_person ? '👤 有人' : '👻 无人'}</span>
                    </div>
                </div>
                
                <div class="zone-card-actions">
                    <button class="btn ${zone.is_fire_on ? 'btn-warning' : 'btn-success'}" 
                            onclick="App.toggleFire('${zone.id}', ${!zone.is_fire_on})">
                        ${zone.is_fire_on ? '🔥 关火' : '🔥 开火'}
                    </button>
                    <button class="btn btn-secondary" onclick="App.resetZone('${zone.id}')">
                        🔄 复位
                    </button>
                </div>
            </div>
        `;
    },

    async toggleFire(zoneId, isOn) {
        try {
            await this.api(`/control/fire/${zoneId}`, 'POST', { is_on: isOn });
            this.toast(isOn ? '已开火' : '已关火', 'success');
            await this.refreshStatus();
        } catch (e) {
            this.toast('操作失败: ' + e.message, 'error');
        }
    },

    async resetZone(zoneId) {
        try {
            await this.api(`/control/reset/${zoneId}`, 'POST');
            this.toast('已复位', 'success');
            await this.refreshStatus();
        } catch (e) {
            this.toast('复位失败: ' + e.message, 'error');
        }
    },

    async resetAllZones() {
        for (const zone of this.statuses) {
            try {
                await this.api(`/control/reset/${zone.id}`, 'POST');
            } catch (e) {
                console.error(`复位 ${zone.id} 失败`, e);
            }
        }
        this.toast('已全部复位', 'success');
        await this.refreshStatus();
    },

    /**
     * 刷新性能数据
     */
    async refreshPerformance() {
        try {
            const data = await this.api('/performance');
            this.updatePerformanceUI(data);
        } catch (e) {
            console.error('获取性能数据失败', e);
        }
    },

    /**
     * 更新性能监控UI
     */
    updatePerformanceUI(data) {
        const setElement = (id, value, colorClass = '') => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = value;
                el.className = 'perf-value ' + colorClass;
            }
        };

        // 推理引擎
        const engineName = data.engine === 'rknn' ? 'RKNN' :
            data.engine === 'pytorch' ? 'PyTorch' : data.engine;
        setElement('perf-engine', engineName);

        // 推理时间
        const inferTime = data.inference_time_ms || 0;
        const inferColor = inferTime < 50 ? 'good' : inferTime < 100 ? 'warning' : 'danger';
        setElement('perf-inference-time', `${inferTime.toFixed(1)} ms`, inferColor);

        // 平均时间
        const avgTime = data.avg_inference_time_ms || 0;
        setElement('perf-avg-time', `${avgTime.toFixed(1)} ms`);

        // FPS
        const fps = data.fps || 0;
        const fpsColor = fps > 20 ? 'good' : fps > 10 ? 'warning' : 'danger';
        setElement('perf-fps', fps.toFixed(1), fpsColor);

        // CPU
        const cpu = data.cpu_percent || 0;
        const cpuColor = cpu < 70 ? 'good' : cpu < 90 ? 'warning' : 'danger';
        setElement('perf-cpu', `${cpu.toFixed(1)}%`, cpuColor);

        // 内存
        const memory = data.memory_mb || 0;
        setElement('perf-memory', `${memory.toFixed(0)} MB`);

        // NPU
        const npu = data.npu_load || 0;
        const npuColor = npu < 70 ? 'good' : npu < 90 ? 'warning' : 'danger';
        setElement('perf-npu', `${npu.toFixed(1)}%`, npuColor);
    },

    // ==================== 摄像头管理 ====================
    // 摄像头刷新定时器
    cameraRefreshTimer: null,

    async initCameras() {
        await this.loadCameras();

        // 每 5 秒自动刷新摄像头状态
        if (this.cameraRefreshTimer) {
            clearInterval(this.cameraRefreshTimer);
        }
        this.cameraRefreshTimer = setInterval(() => this.loadCameras(), 5000);
    },

    async loadCameras() {
        try {
            this.cameras = await this.api('/cameras');
            this.renderCameraList();
        } catch (e) {
            this.toast('加载摄像头失败', 'error');
        }
    },

    renderCameraList() {
        const container = document.getElementById('camera-list');
        if (!container) return;

        if (this.cameras.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📷</div>
                    <p class="empty-state-text">暂无摄像头</p>
                </div>
            `;
            return;
        }

        container.innerHTML = this.cameras.map(cam => `
            <div class="camera-card">
                <div class="camera-icon">📷</div>
                <div class="camera-info">
                    <div class="camera-name">${cam.name}</div>
                    <div class="camera-detail">${cam.type.toUpperCase()} | ${cam.width}x${cam.height}</div>
                </div>
                <span class="camera-status-badge ${cam.status}">${cam.status === 'online' ? '在线' : '离线'}</span>
                <div class="camera-actions">
                    <button class="btn btn-icon btn-secondary" onclick="App.previewCamera('${cam.id}', '${cam.name}')">
                        👁
                    </button>
                    <button class="btn btn-icon btn-secondary" onclick="App.editCamera('${cam.id}')">
                        ✏️
                    </button>
                    <button class="btn btn-icon btn-danger" onclick="App.deleteCamera('${cam.id}')">
                        🗑
                    </button>
                </div>
            </div>
        `).join('');
    },

    showAddCameraModal() {
        const template = document.getElementById('tpl-add-camera-modal');
        document.body.insertAdjacentHTML('beforeend', template.innerHTML);
        // 如果默认显示的是USB，加载设备
        if (document.getElementById('cam-type').value === 'usb') {
            this.loadUsbDevices();
        }
    },

    async loadUsbDevices(selectedValue = null) {
        const select = document.getElementById('cam-device');
        const hint = document.getElementById('usb-scan-hint');
        if (!select) return;

        try {
            if (hint) hint.style.display = 'block';
            select.disabled = true;

            const devices = await this.api('/cameras/devices');

            if (devices && devices.length > 0) {
                select.innerHTML = devices.map(d =>
                    `<option value="${d.index}">${d.name}</option>`
                ).join('');
            } else {
                select.innerHTML = '<option value="-1">未找到可用USB设备</option>';
            }

            if (selectedValue !== null) {
                select.value = selectedValue;
            }
        } catch (e) {
            console.error('加载USB设备失败', e);
            // 保持默或者显示错误
            if (select.options.length === 0) {
                select.innerHTML = '<option value="0">设备 0 (默认)</option>';
            }
        } finally {
            if (hint) hint.style.display = 'none';
            select.disabled = false;
        }
    },

    async toggleCameraTypeFields() {
        const type = document.getElementById('cam-type').value;
        document.querySelectorAll('.usb-field').forEach(el => {
            el.style.display = type === 'usb' ? 'block' : 'none';
        });
        document.querySelectorAll('.rtsp-field').forEach(el => {
            el.style.display = type === 'rtsp' ? 'block' : 'none';
        });

        if (type === 'usb') {
            await this.loadUsbDevices();
        }
    },

    async addCamera() {
        const id = document.getElementById('cam-id').value.trim();
        const name = document.getElementById('cam-name').value.trim();
        const type = document.getElementById('cam-type').value;

        if (!id || !name) {
            this.toast('请填写ID和名称', 'warning');
            return;
        }

        const data = {
            id, name, type,
            width: 640, height: 480, fps: 30
        };

        if (type === 'usb') {
            data.device = parseInt(document.getElementById('cam-device').value) || 0;
        } else {
            data.rtsp_url = document.getElementById('cam-rtsp').value.trim();
            const user = document.getElementById('cam-username').value.trim();
            const pass = document.getElementById('cam-password').value.trim();
            if (user) data.username = user;
            if (pass) data.password = pass;
        }

        try {
            await this.api('/cameras', 'POST', data);
            this.toast(`摄像头 "${name}" 已添加`, 'success');
            this.closeModal();
            await this.loadCameras();
        } catch (e) {
            this.toast('添加失败: ' + e.message, 'error');
        }
    },

    async editCamera(cameraId) {
        const cam = this.cameras.find(c => c.id === cameraId);
        if (!cam) return;

        // 复用添加模态框
        this.showAddCameraModal();

        // 修改标题和按钮
        document.querySelector('.modal-header h3').textContent = '编辑摄像头';
        const submitBtn = document.querySelector('.modal-footer .btn-primary');
        submitBtn.textContent = '保存';
        submitBtn.onclick = () => this.updateCamera(cameraId);

        // 填充数据
        document.getElementById('cam-id').value = cam.id;
        document.getElementById('cam-id').disabled = true; // 禁止修改ID

        document.getElementById('cam-name').value = cam.name;
        const typeSelect = document.getElementById('cam-type');
        typeSelect.value = cam.type;

        // 此处手动控制显示，避免toggleCameraTypeFields默认的不带参数加载重置了选中值
        document.querySelectorAll('.usb-field').forEach(el => {
            el.style.display = cam.type === 'usb' ? 'block' : 'none';
        });
        document.querySelectorAll('.rtsp-field').forEach(el => {
            el.style.display = cam.type === 'rtsp' ? 'block' : 'none';
        });

        if (cam.type === 'usb') {
            // 加载设备并选中
            await this.loadUsbDevices(cam.device !== undefined ? cam.device : 0);
        } else {
            document.getElementById('cam-rtsp').value = cam.rtsp_url || '';
            if (cam.username) document.getElementById('cam-username').value = cam.username;
            if (cam.password) document.getElementById('cam-password').value = cam.password;
        }
    },

    async updateCamera(cameraId) {
        const name = document.getElementById('cam-name').value.trim();
        const type = document.getElementById('cam-type').value;

        if (!name) {
            this.toast('请填写名称', 'warning');
            return;
        }

        const data = {
            id: cameraId,
            name, type,
            width: 640, height: 480, fps: 30
        };

        if (type === 'usb') {
            data.device = parseInt(document.getElementById('cam-device').value) || 0;
        } else {
            data.rtsp_url = document.getElementById('cam-rtsp').value.trim();
            const user = document.getElementById('cam-username').value.trim();
            const pass = document.getElementById('cam-password').value.trim();
            if (user) data.username = user;
            if (pass) data.password = pass;
        }

        try {
            this.toast('正在更新摄像头...', 'info');
            await this.api(`/cameras/${cameraId}`, 'PUT', data);
            this.toast(`摄像头 "${name}" 已更新`, 'success');
            this.closeModal();
            await this.loadCameras();
        } catch (e) {
            this.toast('更新失败: ' + e.message, 'error');
        }
    },

    async deleteCamera(cameraId) {
        if (!confirm('确定删除该摄像头?')) return;

        try {
            await this.api(`/cameras/${cameraId}`, 'DELETE');
            this.toast('已删除', 'success');
            await this.loadCameras();
        } catch (e) {
            this.toast('删除失败: ' + e.message, 'error');
        }
    },

    previewCamera(cameraId, cameraName) {
        const template = document.getElementById('tpl-camera-preview-modal');
        document.body.insertAdjacentHTML('beforeend', template.innerHTML);

        document.getElementById('preview-camera-name').textContent = cameraName;
        const img = document.getElementById('camera-preview-img');
        img.src = `/cameras/${cameraId}/preview?t=${Date.now()}`;

        // 定期刷新
        this._previewInterval = setInterval(() => {
            img.src = `/cameras/${cameraId}/preview?t=${Date.now()}`;
        }, 1000);
    },

    // ==================== 灶台配置 ====================

    async initZones() {
        await this.loadZones();
    },

    async loadZones() {
        try {
            this.zones = await this.api('/zones');
            this.cameras = await this.api('/cameras');
            this.renderZoneConfigList();
        } catch (e) {
            this.toast('加载灶台配置失败', 'error');
        }
    },

    renderZoneConfigList() {
        const container = document.getElementById('zone-config-list');
        if (!container) return;

        if (this.zones.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🍳</div>
                    <p class="empty-state-text">暂无灶台配置</p>
                </div>
            `;
            return;
        }

        container.innerHTML = this.zones.map(zone => `
            <div class="zone-config-card ${zone.enabled ? '' : 'disabled'}">
                <div class="zone-config-header">
                    <span class="zone-config-name">${zone.name}</span>
                    <label class="toggle-switch">
                        <input type="checkbox" ${zone.enabled ? 'checked' : ''} onchange="App.toggleZoneEnabled('${zone.id}', this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
                
                <!-- ROI预览画布 -->
                <div class="roi-preview-container" id="roi-preview-${zone.id}">
                    <img class="roi-preview-bg" data-camera="${zone.camera_id}" data-zone="${zone.id}">
                    <canvas class="roi-preview-canvas" data-roi='${JSON.stringify(zone.roi)}'></canvas>
                    ${!zone.enabled ? '<div class="roi-disabled-overlay">已禁用</div>' : ''}
                </div>
                
                <div class="zone-config-actions">
                    <button class="btn btn-secondary" onclick="App.editRoi('${zone.id}', '${zone.name}', '${zone.camera_id}')">
                        ✏️ 编辑ROI
                    </button>
                    <button class="btn btn-danger" onclick="App.deleteZone('${zone.id}')">
                        🗑 删除
                    </button>
                </div>
            </div>
        `).join('');

        // 加载ROI预览图
        this.loadRoiPreviews();
    },

    /**
     * 切换灶台启用状态
     */
    async toggleZoneEnabled(zoneId, enabled) {
        try {
            await this.api(`/zones/${zoneId}`, 'PUT', { enabled });
            this.toast(enabled ? '灶台已启用' : '灶台已禁用', 'success');
            await this.loadZones();
        } catch (e) {
            this.toast('操作失败: ' + e.message, 'error');
        }
    },

    /**
     * 加载所有灶台的ROI预览图
     */
    loadRoiPreviews() {
        document.querySelectorAll('.roi-preview-bg').forEach(img => {
            const cameraId = img.dataset.camera;
            const zoneId = img.dataset.zone;
            const canvas = img.nextElementSibling;

            img.onload = () => {
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;

                // 获取ROI点并绘制
                const roi = JSON.parse(canvas.dataset.roi || '[]');
                this.drawRoiOnCanvas(canvas, roi);
            };

            img.src = `/cameras/${cameraId}/preview?t=${Date.now()}`;
        });
    },

    /**
     * 在指定canvas上绘制ROI区域
     */
    drawRoiOnCanvas(canvas, roiPoints) {
        if (!canvas || roiPoints.length < 3) return;

        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        ctx.beginPath();
        ctx.strokeStyle = '#27ae60';
        ctx.fillStyle = 'rgba(39, 174, 96, 0.3)';
        ctx.lineWidth = 2;

        roiPoints.forEach((point, i) => {
            const x = point[0] * canvas.width;
            const y = point[1] * canvas.height;

            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });

        ctx.closePath();
        ctx.fill();
        ctx.stroke();

        // 绘制顶点
        roiPoints.forEach(point => {
            const x = point[0] * canvas.width;
            const y = point[1] * canvas.height;
            ctx.beginPath();
            ctx.fillStyle = '#27ae60';
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fill();
        });
    },

    showAddZoneModal() {
        const template = document.getElementById('tpl-add-zone-modal');
        document.body.insertAdjacentHTML('beforeend', template.innerHTML);

        // 填充摄像头选项
        const select = document.getElementById('zone-camera');
        select.innerHTML = this.cameras.map(cam =>
            `<option value="${cam.id}">${cam.name}</option>`
        ).join('');
    },

    async addZone() {
        const name = document.getElementById('zone-name').value.trim();
        const cameraId = document.getElementById('zone-camera').value;

        if (!name) {
            this.toast('请输入灶台名称', 'warning');
            return;
        }

        // 自动生成灶台ID
        const existingIds = this.zones.map(z => z.id);
        let zoneNum = this.zones.length + 1;
        let zoneId = `zone_${zoneNum}`;
        while (existingIds.includes(zoneId)) {
            zoneNum++;
            zoneId = `zone_${zoneNum}`;
        }

        const data = {
            id: zoneId,
            name: name,
            camera_id: cameraId,
            roi: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],  // 默认ROI
            enabled: true
        };

        try {
            await this.api('/zones', 'POST', data);
            this.toast(`灶台 "${name}" 已添加`, 'success');
            this.closeModal();
            await this.loadZones();
        } catch (e) {
            this.toast('添加失败: ' + e.message, 'error');
        }
    },

    async deleteZone(zoneId) {
        if (!confirm('确定删除该灶台?')) return;

        try {
            await this.api(`/zones/${zoneId}`, 'DELETE');
            this.toast('已删除', 'success');
            await this.loadZones();
        } catch (e) {
            this.toast('删除失败: ' + e.message, 'error');
        }
    },

    // ==================== ROI编辑 ====================

    editRoi(zoneId, zoneName, cameraId) {
        this.roiZoneId = zoneId;
        this.roiCameraId = cameraId;

        // 加载已有的ROI点
        const zone = this.zones.find(z => z.id === zoneId);
        this.roiPoints = zone && zone.roi ? [...zone.roi] : [];

        const template = document.getElementById('tpl-roi-editor-modal');
        document.body.insertAdjacentHTML('beforeend', template.innerHTML);

        document.getElementById('roi-zone-name').textContent = zoneName;

        // 加载摄像头预览
        const img = document.getElementById('roi-preview');
        const canvas = document.getElementById('roi-canvas');

        img.onload = () => {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            // 绘制已有的ROI区域
            this.drawRoi();
        };

        img.src = `/cameras/${cameraId}/preview?t=${Date.now()}`;

        // 绑定点击事件
        canvas.addEventListener('click', (e) => this.handleRoiClick(e));
        canvas.addEventListener('touchend', (e) => {
            e.preventDefault();
            const touch = e.changedTouches[0];
            this.handleRoiClick({
                offsetX: touch.clientX - canvas.getBoundingClientRect().left,
                offsetY: touch.clientY - canvas.getBoundingClientRect().top,
                target: canvas
            });
        });
    },

    handleRoiClick(e) {
        const canvas = e.target;
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;

        const x = e.offsetX * scaleX;
        const y = e.offsetY * scaleY;

        // 归一化坐标
        const normX = x / canvas.width;
        const normY = y / canvas.height;

        this.roiPoints.push([normX, normY]);
        this.drawRoi();
    },

    drawRoi() {
        const canvas = document.getElementById('roi-canvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        if (this.roiPoints.length === 0) return;

        // 计算所有点的像素坐标
        const pixelPoints = this.roiPoints.map(point => ({
            x: point[0] * canvas.width,
            y: point[1] * canvas.height
        }));

        // 如果有3个以上的点，先绘制填充区域
        if (pixelPoints.length >= 3) {
            ctx.beginPath();
            ctx.moveTo(pixelPoints[0].x, pixelPoints[0].y);
            for (let i = 1; i < pixelPoints.length; i++) {
                ctx.lineTo(pixelPoints[i].x, pixelPoints[i].y);
            }
            ctx.closePath();
            ctx.fillStyle = 'rgba(39, 174, 96, 0.25)';
            ctx.fill();
        }

        // 绘制连接线
        if (pixelPoints.length >= 2) {
            ctx.beginPath();
            ctx.strokeStyle = '#27ae60';
            ctx.lineWidth = 3;
            ctx.setLineDash([]);

            ctx.moveTo(pixelPoints[0].x, pixelPoints[0].y);
            for (let i = 1; i < pixelPoints.length; i++) {
                ctx.lineTo(pixelPoints[i].x, pixelPoints[i].y);
            }

            // 如果有3个以上的点，用虚线显示将要封闭的线
            if (pixelPoints.length >= 3) {
                ctx.stroke();
                // 绘制封闭线（虚线表示预览）
                ctx.beginPath();
                ctx.setLineDash([8, 4]);
                ctx.strokeStyle = 'rgba(39, 174, 96, 0.6)';
                ctx.moveTo(pixelPoints[pixelPoints.length - 1].x, pixelPoints[pixelPoints.length - 1].y);
                ctx.lineTo(pixelPoints[0].x, pixelPoints[0].y);
                ctx.stroke();
            } else {
                ctx.stroke();
            }
        }

        // 绘制顶点和序号
        pixelPoints.forEach((point, i) => {
            // 顶点圆圈
            ctx.beginPath();
            ctx.setLineDash([]);

            // 第一个点用不同颜色标记
            if (i === 0) {
                ctx.fillStyle = '#e74c3c';  // 红色表示起点
                ctx.strokeStyle = '#fff';
            } else {
                ctx.fillStyle = '#27ae60';
                ctx.strokeStyle = '#fff';
            }

            ctx.arc(point.x, point.y, 12, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.stroke();

            // 绘制序号
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 12px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText((i + 1).toString(), point.x, point.y);
        });

        // 显示提示信息
        this.updateRoiInstructions();
    },

    /**
     * 更新ROI编辑提示信息
     */
    updateRoiInstructions() {
        const instructions = document.querySelector('.roi-instructions p');
        if (!instructions) return;

        const count = this.roiPoints.length;
        if (count === 0) {
            instructions.textContent = '点击画面添加第1个顶点（起点，红色标记）';
        } else if (count === 1) {
            instructions.textContent = '继续点击添加第2个顶点';
        } else if (count === 2) {
            instructions.textContent = '继续点击添加第3个顶点（至少需要3个点形成区域）';
        } else {
            instructions.textContent = `已添加 ${count} 个顶点，虚线表示封闭区域。可继续添加或保存`;
        }
    },

    clearRoi() {
        this.roiPoints = [];
        this.drawRoi();
    },

    undoRoiPoint() {
        this.roiPoints.pop();
        this.drawRoi();
    },

    async saveRoi() {
        if (this.roiPoints.length < 3) {
            this.toast('请至少添加3个顶点', 'warning');
            return;
        }

        try {
            await this.api(`/zones/${this.roiZoneId}`, 'PUT', {
                roi: this.roiPoints
            });
            this.toast('ROI已保存', 'success');
            this.closeModal();
            await this.loadZones();
        } catch (e) {
            this.toast('保存失败: ' + e.message, 'error');
        }
    },

    // ==================== 日志 ====================

    async initLogs() {
        await this.loadLogFiles();
        await this.loadLogContent();
    },



    async loadLogFiles() {
        try {
            const data = await this.api('/logs/list');
            const select = document.getElementById('log-file-selector');
            if (select && data.files) {
                select.innerHTML = data.files.map(f => `
                    <option value="${f.name}">${f.name} (${(f.size / 1024).toFixed(1)} KB)</option>
                `).join('');
            }
        } catch (e) {
            console.error('加载日志文件列表失败', e);
        }
    },

    async loadLogContent(filename = '') {
        const container = document.getElementById('log-content');
        if (container) {
            container.innerText = '正在读取日志内容...';
        }

        try {
            const url = filename ? `/logs/read?filename=${encodeURIComponent(filename)}` : '/logs/read';
            const data = await this.api(url);
            if (container) {
                container.innerText = data.content || '日志为空';
                // 滚动到底部
                const viewer = container.parentElement;
                viewer.scrollTop = viewer.scrollHeight;
            }
        } catch (e) {
            if (container) container.innerText = '读取失败: ' + e.message;
        }
    },

    async refreshLogs() {
        const select = document.getElementById('log-file-selector');
        const currentFile = select ? select.value : '';

        await this.loadLogFiles();

        // 如果之前选了文件，刷新后继续显示该文件
        if (currentFile) {
            if (select) select.value = currentFile;
            await this.loadLogContent(currentFile);
        } else {
            await this.loadLogContent();
        }

        this.toast('日志已刷新', 'info');
    },

    // ==================== 设置 ====================

    /**
     * 测试 TTS 语音播报
     */
    async testTTS() {
        const textInput = document.getElementById('tts-test-text');
        const text = textInput ? textInput.value.trim() : '';

        try {
            const result = await this.api('/voice/test', 'POST', {
                text: text || null
            });

            if (result.success) {
                this.toast('语音测试已发送', 'success');
            } else {
                this.toast(result.message || '语音测试失败', 'error');
            }
        } catch (e) {
            this.toast('语音测试失败: ' + e.message, 'error');
        }
    },

    async initSettings() {
        await this.loadDeviceDetailInfo();
        this.loadGlobalSettings();
    },

    /**
     * 加载全局设置到表单
     */
    async loadGlobalSettings() {
        try {
            const settings = await this.api('/settings/safety');
            this.globalSettings.warningTimeout = settings.warning_timeout;
            this.globalSettings.cutoffTimeout = settings.cutoff_timeout;

            // 填充表单
            const warningInput = document.getElementById('global-warning-timeout');
            const cutoffInput = document.getElementById('global-cutoff-timeout');

            if (warningInput) warningInput.value = settings.warning_timeout;
            if (cutoffInput) cutoffInput.value = settings.cutoff_timeout;
        } catch (e) {
            console.error('加载全局设置失败', e);
            this.toast('加载设置失败', 'error');
        }
    },

    async loadDeviceDetailInfo() {
        try {
            const info = await this.api('/device');
            const container = document.getElementById('device-detail-info');
            if (container) {
                container.innerHTML = `
                    <div class="info-row">
                        <span class="info-label">系统名称</span>
                        <span class="info-value">${info.name}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">版本</span>
                        <span class="info-value">${info.version}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">运行时间</span>
                        <span class="info-value">${this.formatUptime(info.uptime)}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">平台</span>
                        <span class="info-value">${info.platform}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Python版本</span>
                        <span class="info-value">${info.python_version}</span>
                    </div>
                `;
            }
        } catch (e) {
            console.error('加载设备信息失败', e);
        }
    },

    formatUptime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        return `${hours}小时 ${minutes}分钟`;
    },

    async saveGlobalSettings() {
        const warningTimeout = parseInt(document.getElementById('global-warning-timeout')?.value) || 30;
        const cutoffTimeout = parseInt(document.getElementById('global-cutoff-timeout')?.value) || 60;

        // 验证
        if (warningTimeout < 5 || warningTimeout > 300) {
            this.toast('预警超时时间需在5-300秒之间', 'warning');
            return;
        }
        if (cutoffTimeout < 10 || cutoffTimeout > 600) {
            this.toast('切电超时时间需在10-600秒之间', 'warning');
            return;
        }
        if (cutoffTimeout <= warningTimeout) {
            this.toast('切电超时时间需大于预警超时时间', 'warning');
            return;
        }

        try {
            // 保存到后端
            await this.api('/settings/safety', 'POST', {
                warning_timeout: warningTimeout,
                cutoff_timeout: cutoffTimeout
            });

            this.globalSettings = { warningTimeout, cutoffTimeout };
            this.toast(`全局设置已保存：预警${warningTimeout}秒，切电${cutoffTimeout}秒`, 'success');
        } catch (e) {
            this.toast('保存设置失败: ' + e.message, 'error');
        }
    },

    restartSystem() {
        if (confirm('确定要重启系统吗?')) {
            this.toast('重启命令已发送', 'info');
        }
    },

    // ==================== WebSocket ====================

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/status`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('WebSocket已连接');
                document.getElementById('connection-status').classList.remove('offline');
                document.getElementById('connection-status').classList.add('online');
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWsMessage(data);
            };

            this.ws.onclose = () => {
                console.log('WebSocket已断开');
                document.getElementById('connection-status').classList.remove('online');
                document.getElementById('connection-status').classList.add('offline');

                // 重连
                this.wsReconnectTimer = setTimeout(() => this.connectWebSocket(), 3000);
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket错误', error);
            };
        } catch (e) {
            console.error('WebSocket连接失败', e);
        }
    },

    handleWsMessage(data) {
        if (data.type === 'status_update') {
            this.statuses = data.data;
            if (this.currentPage === 'dashboard') {
                this.renderZoneCards();
            }
        } else if (data.type === 'state_change') {
            const event = data.data;
            if (event.new_state === 'warning') {
                this.toast(`⚠️ ${event.zone_name} 无人看管预警`, 'warning');
            } else if (event.new_state === 'cutoff') {
                this.toast(`🔴 ${event.zone_name} 已自动切电`, 'error');
            }
        }
    },

    // ==================== 工具方法 ====================

    async api(endpoint, method = 'GET', data = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (data) {
            options.body = JSON.stringify(data);
        }

        const response = await fetch(this.apiBase + endpoint, options);

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || response.statusText);
        }

        return response.json();
    },

    closeModal() {
        const overlay = document.querySelector('.modal-overlay');
        if (overlay) {
            overlay.remove();
        }

        // 清除预览定时器
        if (this._previewInterval) {
            clearInterval(this._previewInterval);
            this._previewInterval = null;
        }
    },

    toast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const icons = {
            success: '✅',
            error: '❌',
            warning: '⚠️',
            info: 'ℹ️'
        };

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type]}</span>
            <span class="toast-message">${message}</span>
        `;

        container.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(-20px)';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
};

// 启动应用
document.addEventListener('DOMContentLoaded', () => App.init());
