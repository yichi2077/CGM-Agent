// Charts for CGM Agent Competitive Analysis
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var green = style.getPropertyValue('--green').trim();
  var yellow = style.getPropertyValue('--yellow').trim();
  var red = style.getPropertyValue('--red').trim();
  var blue = style.getPropertyValue('--accent4').trim();

  // --- Chart 1: Feature Completeness Radar ---
  var radarEl = document.getElementById('chart-radar');
  if (radarEl) {
    var radar = echarts.init(radarEl, null, { renderer: 'svg' });
    radar.setOption({
      animation: false,
      tooltip: { appendToBody: true },
      radar: {
        indicator: [
          { name: '数据采集', max: 100 },
          { name: '指标计算', max: 100 },
          { name: '事件检测', max: 100 },
          { name: '记忆系统', max: 100 },
          { name: 'RAG/KB', max: 100 },
          { name: '安全门控', max: 100 },
          { name: '报告叙事', max: 100 },
          { name: '推送投递', max: 100 },
          { name: '硬件接入', max: 100 },
          { name: 'AGP可视化', max: 100 }
        ],
        shape: 'polygon',
        splitNumber: 4,
        axisName: { color: ink, fontSize: 12 },
        splitLine: { lineStyle: { color: rule } },
        splitArea: { areaStyle: { color: ['transparent', bg2] } },
        axisLine: { lineStyle: { color: rule } }
      },
      series: [{
        type: 'radar',
        data: [{
          value: [70, 85, 50, 90, 55, 90, 65, 50, 40, 10],
          name: '当前完成度',
          areaStyle: { color: accent + '33' },
          lineStyle: { color: accent, width: 2 },
          itemStyle: { color: accent }
        }]
      }]
    });
    window.addEventListener('resize', function() { radar.resize(); });
  }

  // --- Chart 2: Competitive Positioning Map ---
  var scatterEl = document.getElementById('chart-positioning');
  if (scatterEl) {
    var scatter = echarts.init(scatterEl, null, { renderer: 'svg' });
    scatter.setOption({
      animation: false,
      tooltip: {
        appendToBody: true,
        formatter: function(p) {
          return p.data[3] + '<br/>AI陪伴深度: ' + p.data[0] + '<br/>数据隐私/本地化: ' + p.data[1];
        }
      },
      grid: { left: 60, right: 40, top: 40, bottom: 60 },
      xAxis: {
        name: 'AI陪伴/智能深度 →',
        nameLocation: 'middle',
        nameGap: 35,
        min: 0, max: 100,
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      yAxis: {
        name: '↑ 数据隐私/本地化',
        nameLocation: 'middle',
        nameGap: 45,
        min: 0, max: 100,
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      series: [{
        type: 'scatter',
        symbolSize: function(d) { return d[2]; },
        data: [
          [85, 90, 28, 'Hermes CGM Agent', accent],
          [10, 90, 22, 'Nightscout', blue],
          [5, 20, 24, 'xDrip+', blue],
          [15, 15, 30, 'Dexcom Clarity', red],
          [10, 15, 28, 'Abbott LibreView', red],
          [20, 10, 22, 'Medtronic SugarIQ', red],
          [40, 10, 20, 'One Drop', yellow],
          [35, 10, 22, 'Levels Health', yellow],
          [30, 10, 18, 'Veri', yellow],
          [15, 10, 20, 'mySugr', yellow]
        ],
        itemStyle: {
          color: function(p) { return p.data[4]; },
          opacity: 0.8
        },
        label: {
          show: true,
          formatter: function(p) { return p.data[3]; },
          position: 'top',
          fontSize: 11,
          color: ink
        }
      },
      {
        type: 'scatter',
        data: [],
        markLine: {
          silent: true,
          lineStyle: { color: rule, type: 'solid' },
          data: [
            { xAxis: 50, lineStyle: { type: 'dashed' } },
            { yAxis: 50, lineStyle: { type: 'dashed' } }
          ]
        }
      }]
    });
    window.addEventListener('resize', function() { scatter.resize(); });
  }

  // --- Chart 3: Priority Effort/Impact Matrix ---
  var matrixEl = document.getElementById('chart-priority');
  if (matrixEl) {
    var matrix = echarts.init(matrixEl, null, { renderer: 'svg' });
    matrix.setOption({
      animation: false,
      tooltip: {
        appendToBody: true,
        formatter: function(p) {
          return p.data[3];
        }
      },
      grid: { left: 60, right: 40, top: 40, bottom: 60 },
      xAxis: {
        name: '实施工作量 →',
        nameLocation: 'middle',
        nameGap: 35,
        min: 0, max: 10,
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: muted, formatter: function(v) { return v === 0 ? '低' : v === 10 ? '高' : ''; } },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      yAxis: {
        name: '↑ 影响/价值',
        nameLocation: 'middle',
        nameGap: 45,
        min: 0, max: 10,
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: muted, formatter: function(v) { return v === 0 ? '低' : v === 10 ? '高' : ''; } },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      series: [{
        type: 'scatter',
        symbolSize: 22,
        data: [
          [2, 10, '修复事件检测管道', green],
          [5, 10, 'KB临床审核（30-50张核心卡）', green],
          [2, 9, '验证DB密钥持久化', green],
          [3, 8, '修复报告叙事幻觉', green],
          [3, 8, '实现报告安全审查', green],
          [4, 7, '接入真实硬件(AiDEX/xDrip)', yellow],
          [5, 6, 'AGP可视化', yellow],
          [2, 6, '激活脆弱人群适配', yellow],
          [3, 5, '完善推送投递通道', yellow],
          [4, 7, '用户一键安装/降低门槛', yellow],
          [6, 5, '审计日志查询API', blue],
          [5, 4, '拆分大模块(cli/builder)', blue],
          [8, 6, 'Web UI/仪表板', blue],
          [6, 3, '语义检索默认启用', muted]
        ],
        itemStyle: {
          color: function(p) { return p.data[3]; },
          opacity: 0.85
        },
        label: {
          show: true,
          formatter: function(p) { return p.data[2]; },
          position: 'right',
          fontSize: 10,
          color: ink
        }
      }],
      graphic: [
        { type: 'rect', left: 60, top: 40, shape: { width: 400, height: 220 }, style: { fill: green + '11' }, z: -1 },
        { type: 'text', left: 70, top: 50, style: { text: 'Quick Wins (优先)', fill: green, fontSize: 12, fontWeight: 'bold' } }
      ]
    });
    window.addEventListener('resize', function() { matrix.resize(); });
  }
})();
