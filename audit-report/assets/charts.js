// CGM-Agent 审计报告图表逻辑
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var crit = style.getPropertyValue('--crit').trim();
  var high = style.getPropertyValue('--high').trim();
  var med = style.getPropertyValue('--med').trim();
  var low = style.getPropertyValue('--low').trim();
  var pass = style.getPropertyValue('--pass').trim();

  // ========== Chart 1: 严重级别分布 (环形图) ==========
  var chart1 = echarts.init(document.getElementById('chart-severity'), null, { renderer: 'svg' });
  chart1.setOption({
    tooltip: { trigger: 'item', appendToBody: true },
    legend: { bottom: 10, textStyle: { color: muted } },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: bg2, borderWidth: 2 },
      label: { show: true, color: ink, fontSize: 13, formatter: '{b}\n{c}项' },
      emphasis: { label: { fontSize: 16, fontWeight: 'bold' } },
      data: [
        { value: 4, name: 'CRITICAL', itemStyle: { color: crit } },
        { value: 13, name: 'HIGH', itemStyle: { color: high } },
        { value: 28, name: 'MEDIUM', itemStyle: { color: med } },
        { value: 29, name: 'LOW', itemStyle: { color: low } }
      ]
    }]
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // ========== Chart 2: 各模块发现数量 (堆叠柱状图) ==========
  var chart2 = echarts.init(document.getElementById('chart-module'), null, { renderer: 'svg' });
  chart2.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true },
    legend: { bottom: 5, textStyle: { color: muted } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '5%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['安全路由器', 'RAG 系统', '记忆系统', '报告生成', '工具系统', '系统基础设施', '跨模块集成'],
      axisLabel: { color: muted, fontSize: 11, rotate: 0, interval: 0 },
      axisLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: muted },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } }
    },
    series: [
      { name: 'CRITICAL', type: 'bar', stack: 'total', itemStyle: { color: crit }, data: [1, 0, 3, 0, 0, 0, 0] },
      { name: 'HIGH', type: 'bar', stack: 'total', itemStyle: { color: high }, data: [1, 2, 5, 1, 0, 3, 1] },
      { name: 'MEDIUM', type: 'bar', stack: 'total', itemStyle: { color: med }, data: [2, 2, 4, 5, 5, 6, 4] },
      { name: 'LOW', type: 'bar', stack: 'total', itemStyle: { color: low }, data: [0, 3, 2, 7, 5, 7, 5] }
    ]
  });
  window.addEventListener('resize', function() { chart2.resize(); });

  // ========== Chart 3: 修复优先级矩阵 (散点图) ==========
  var chart3 = echarts.init(document.getElementById('chart-priority'), null, { renderer: 'svg' });
  chart3.setOption({
    tooltip: {
      formatter: function(params) {
        return params.data.name + '<br/>影响: ' + params.data.value[1] + '/10<br/>紧急度: ' + params.data.value[0] + '/10';
      },
      appendToBody: true
    },
    grid: { left: '8%', right: '12%', bottom: '12%', top: '8%' },
    xAxis: {
      name: '紧急度 →',
      nameLocation: 'end',
      nameTextStyle: { color: muted, fontSize: 12 },
      min: 0, max: 11,
      axisLabel: { color: muted },
      splitLine: { show: true, lineStyle: { color: rule, type: 'dashed', opacity: 0.3 } },
      axisLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      name: '影响 →',
      nameLocation: 'end',
      nameTextStyle: { color: muted, fontSize: 12 },
      min: 0, max: 11,
      axisLabel: { color: muted },
      splitLine: { show: true, lineStyle: { color: rule, type: 'dashed', opacity: 0.3 } },
      axisLine: { lineStyle: { color: rule } }
    },
    series: [
      {
        name: 'CRITICAL',
        type: 'scatter',
        symbolSize: 22,
        itemStyle: { color: crit, opacity: 0.85 },
        label: { show: true, formatter: '{b}', position: 'right', color: ink, fontSize: 10, distance: 8 },
        data: [
          { name: 'C-01', value: [10, 10] },
          { name: 'C-02', value: [8, 9] },
          { name: 'C-03', value: [9, 8] },
          { name: 'C-04', value: [8, 8] }
        ]
      },
      {
        name: 'HIGH',
        type: 'scatter',
        symbolSize: 18,
        itemStyle: { color: high, opacity: 0.85 },
        label: { show: true, formatter: '{b}', position: 'right', color: ink, fontSize: 9, distance: 6 },
        data: [
          { name: 'H-01', value: [5, 8] },
          { name: 'H-02', value: [3, 7] },
          { name: 'H-03', value: [3, 7] },
          { name: 'H-04', value: [6, 7] },
          { name: 'H-05', value: [7, 7] },
          { name: 'H-06', value: [5, 6] },
          { name: 'H-07', value: [4, 6] },
          { name: 'H-08', value: [5, 6] },
          { name: 'H-09', value: [7, 8] },
          { name: 'H-10', value: [4, 5] },
          { name: 'H-11', value: [6, 9] },
          { name: 'H-12', value: [5, 9] },
          { name: 'H-13', value: [6, 6] }
        ]
      },
      {
        name: 'MEDIUM/LOW',
        type: 'scatter',
        symbolSize: 10,
        itemStyle: { color: med, opacity: 0.6 },
        data: [
          { name: 'M群', value: [4, 4] }, { name: 'M群', value: [3, 5] },
          { name: 'M群', value: [5, 3] }, { name: 'M群', value: [3, 3] },
          { name: 'M群', value: [2, 4] }, { name: 'M群', value: [4, 2] },
          { name: 'L群', value: [2, 2] }, { name: 'L群', value: [1, 3] },
          { name: 'L群', value: [2, 1] }, { name: 'L群', value: [1, 1] },
          { name: 'L群', value: [1, 2] }, { name: 'L群', value: [2, 3] }
        ]
      }
    ],
    legend: {
      bottom: 0,
      textStyle: { color: muted },
      data: ['CRITICAL', 'HIGH', 'MEDIUM/LOW']
    },
    markLine: {
      silent: true,
      symbol: 'none',
      lineStyle: { color: rule, type: 'dashed', opacity: 0.5 },
      data: [
        { xAxis: 5.5 },
        { yAxis: 5.5 }
      ]
    },
    markArea: {
      silent: true,
      itemStyle: { color: 'rgba(220,38,38,0.04)' },
      data: [[{ xAxis: 5.5 }, { xAxis: 11, yAxis: 11 }, { yAxis: 5.5 }]]
    }
  });
  window.addEventListener('resize', function() { chart3.resize(); });
})();
