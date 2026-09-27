import { api } from './api.js';

const $ = selector => document.querySelector(selector);
let graph = null;
let chatAbort = null;

export function initTerminal() {
  $('#terminalSendBtn')?.addEventListener('click', sendTerminalChat);
  $('#terminalInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') sendTerminalChat();
  });
  $('#graphLoadAllBtn')?.addEventListener('click', loadFullGraph);
}

// 视图切换离开时中止未完成的 SSE 流，避免继续写隐藏 DOM / 驱动图谱
export function deactivateTerminal() {
  if (chatAbort) {
    chatAbort.abort();
    chatAbort = null;
  }
}

export function activateTerminalView() {
  requestAnimationFrame(() => {
    if (!graph && window.d3) {
      try {
        graph = new MemoryGraph('#graphCanvas');
      } catch (e) {
        console.warn('初始化图谱异常', e);
      }
    }
    if (graph && graph.nodes && graph.nodes.length === 0) {
      loadFullGraph();
    }
  });
}

function appendChatBubble(role, text) {
  const feed = $('#chatFeed');
  if (!feed) return;
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = text;
  feed.appendChild(bubble);
  feed.scrollTop = feed.scrollHeight;
}

function dispatchSseBlock(part) {
  if (!part || !part.trim()) return;
  const lines = part.split('\n');
  let event = 'message';
  let dataStr = '';
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      event = line.substring(7).trim();
    } else if (line.startsWith('data: ')) {
      dataStr = line.substring(6).trim();
    }
  }
  if (!dataStr) return;
  try {
    handleSseEvent(event, JSON.parse(dataStr));
  } catch (err) {
    console.error('SSE JSON 解析错误', err);
  }
}

async function sendTerminalChat() {
  const input = $('#terminalInput');
  if (!input) return;
  const query = input.value.trim();
  if (!query) return;

  const domain = $('#terminalDomain')?.value || 'user';
  input.value = '';
  appendChatBubble('user', query);

  const btn = $('#terminalSendBtn');
  if (btn) btn.disabled = true;

  // 新请求中止上一轮未完成的流
  if (chatAbort) chatAbort.abort();
  chatAbort = new AbortController();
  const { signal } = chatAbort;

  try {
    const key = sessionStorage.getItem('lantai-api-key-session') || localStorage.getItem('lantai_api_key') || '';
    
    if (graph) graph.clearHighlights();
    
    const response = await fetch('/terminal/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(key ? {'X-API-Key': key} : {})
      },
      body: JSON.stringify({ query, domain, force: true, top_k: 8 }),
      signal,
    });

    if (!response.ok) {
      throw new Error(response.status === 401
        ? '未授权（401）：请在「连接设置」中更新 API Key'
        : '网络异常: ' + response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();

      for (const part of parts) dispatchSseBlock(part);
    }

    // 处理流末尾未以 \n\n 收尾的残余帧（complete/error 常在此丢失）
    buffer += decoder.decode();
    dispatchSseBlock(buffer);
    
    if (!signal.aborted) {
      appendChatBubble('agent', '已完成检索与拓扑链路分析。可以在右侧图谱观察唤醒的记忆。');
    }

  } catch (err) {
    if (err.name === 'AbortError') {
      appendChatBubble('system', '[已中止] 上一轮检索已取消。');
    } else {
      appendChatBubble('system', `检索失败: ${err.message}`);
    }
  } finally {
    if (chatAbort && chatAbort.signal === signal) chatAbort = null;
    if (btn) btn.disabled = false;
  }
}

function handleSseEvent(event, data) {
  if (event === 'step') {
    appendChatBubble('system', `[系统] ${data.message}`);
    const st = $('#graphStatus');
    if (st) st.textContent = data.message;
  } else if (event === 'gate') {
    if (!data.needs_memory) {
      appendChatBubble('system', `[闸门拦截] ${data.reason}`);
    }
  } else if (event === 'node_hit') {
    if (graph) {
      graph.addOrUpdateNode(data.node);
      graph.highlightNode(data.node.id);
    }
  } else if (event === 'edges') {
    if (graph && data.edges) {
      data.edges.forEach(e => graph.addOrUpdateEdge(e));
      graph.highlightEdges(data.edges);
    }
  } else if (event === 'complete') {
    const st = $('#graphStatus');
    if (st) st.textContent = data.message;
  } else if (event === 'error') {
    appendChatBubble('system', `[异常] ${data.message}`);
  }
}

async function loadFullGraph() {
  const btn = $('#graphLoadAllBtn');
  if (btn) btn.disabled = true;
  const st = $('#graphStatus');
  if (st) st.textContent = '加载全库拓扑...';
  
  try {
    const data = await api('/terminal/graph?limit=100');
    if (graph) {
      graph.setData(data.nodes || [], data.edges || []);
      if (st) st.textContent = `全图谱 (节点: ${(data.nodes || []).length})`;
    }
  } catch (err) {
    if (st) st.textContent = '加载失败: ' + err.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* --------------- 简单的 D3 图谱封装 --------------- */
class MemoryGraph {
  constructor(selector) {
    // 先初始化字段：#graphCanvas 缺失时提前 return 也不会留下半成品对象
    this.nodes = [];
    this.links = [];
    this.mode = 'view';
    this.selectedNodeId = null;
    this._suppressClick = false;
    this._dragMoved = false;
    this._updateQueued = false;
    const el = document.querySelector(selector);
    if (!el) return;
    this.svg = d3.select(selector);
    const rect = el.getBoundingClientRect();
    this.width = rect.width || 600;
    this.height = rect.height || 500;
    
    this.initSimulation();
    this.initCanvas();
    this.initEditor();
  }

  initSimulation() {
    this.simulation = d3.forceSimulation()
      .force("link", d3.forceLink().id(d => d.id).distance(100))
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(this.width / 2, this.height / 2))
      .force("collide", d3.forceCollide().radius(30));
  }

  initCanvas() {
    this.svg.selectAll("*").remove();
    this.g = this.svg.append("g");
    
    const zoom = d3.zoom()
      .scaleExtent([0.1, 4])
      .on("zoom", (e) => this.g.attr("transform", e.transform));
    this.svg.call(zoom);

    this.linkGroup = this.g.append("g").attr("class", "links");
    this.nodeGroup = this.g.append("g").attr("class", "nodes");
  }

  initEditor() {
    $('#nodeEditorClose')?.addEventListener('click', () => {
      const ed = $('#nodeEditor');
      if (ed) ed.hidden = true;
      this.mode = 'view';
      this.clearHighlights();
    });
    
    $('#neSaveBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      try {
        await api(`/terminal/memory/${id}`, {
          method: 'PUT',
          body: JSON.stringify({
            content: $('#neContent')?.value,
            importance: parseFloat($('#neImportance')?.value || 0.8),
            confidence: parseFloat($('#neConfidence')?.value || 0.9),
            memory_type: $('#neType')?.value || 'semantic'
          })
        });
        const node = this.nodes.find(n => n.id === id);
        if (node) {
          node.content = $('#neContent')?.value;
          this.updateView();
        }
        alert('保存成功');
      } catch (err) {
        alert('保存失败: ' + err.message);
      }
    });

    $('#neDeleteBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      if (!confirm('确定删除此记忆？')) return;
      try {
        await api(`/terminal/memory/${id}`, { method: 'DELETE' });
        this.nodes = this.nodes.filter(n => n.id !== id);
        this.links = this.links.filter(l => (l.source.id || l.source) !== id && (l.target.id || l.target) !== id);
        this.updateView();
        const ed = $('#nodeEditor');
        if (ed) ed.hidden = true;
      } catch (err) {
        alert('删除失败: ' + err.message);
      }
    });

    $('#neLinkBtn')?.addEventListener('click', () => {
      this.mode = 'link';
      this.selectedNodeId = $('#neId')?.value;
      const title = $('#nodeEditorTitle');
      if (title) title.textContent = '点击另一个节点建立关联...';
    });
    
    $('#neMergeBtn')?.addEventListener('click', () => {
      this.mode = 'merge';
      this.selectedNodeId = $('#neId')?.value;
      const title = $('#nodeEditorTitle');
      if (title) title.textContent = '点击另一个节点合并...';
    });

    // ===== 笔削七操作（可逆侧前端暴露：纠/撤/藏） =====
    $('#neCorrectBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      const node = this.nodes.find(n => n.id === id);
      const content = prompt('输入改正后的记忆内容：', node?.content || '');
      if (content === null) return;
      if (!content.trim()) { alert('内容不能为空'); return; }
      const reason = prompt('纠错原因（建议留痕）：', '') ?? '';
      try {
        await api(`/terminal/memory/${id}/correct`, {
          method: 'POST',
          body: JSON.stringify({content: content.trim(), reason}),
        });
        if (node) { node.content = content.trim(); this.updateView(); }
        const ed = $('#neContent');
        if (ed) ed.value = content.trim();
        alert('已纠错，版本留痕可查');
      } catch (err) {
        alert('纠错失败: ' + err.message);
      }
    });

    $('#neRetractBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      const reason = prompt('撤回原因（必填，主张停用必须留痕）：', '');
      if (reason === null) return;
      if (!reason.trim()) { alert('撤回必须填写原因'); return; }
      try {
        await api(`/terminal/memory/${id}/retract`, {
          method: 'POST',
          body: JSON.stringify({reason: reason.trim()}),
        });
        // 撤回后退出常规检索：从星图移除
        this.nodes = this.nodes.filter(n => n.id !== id);
        this.links = this.links.filter(l => (l.source.id || l.source) !== id && (l.target.id || l.target) !== id);
        this.updateView();
        const ed = $('#nodeEditor');
        if (ed) ed.hidden = true;
        alert('已撤回（可在服务端 unretract 恢复）');
      } catch (err) {
        alert('撤回失败: ' + err.message);
      }
    });

    $('#neArchiveBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      if (!confirm('归档后该记忆退出常规检索（可逆，服务端 unarchive 恢复）。确认归档？')) return;
      try {
        await api(`/terminal/memory/${id}/archive`, {method: 'POST', body: JSON.stringify({})});
        this.nodes = this.nodes.filter(n => n.id !== id);
        this.links = this.links.filter(l => (l.source.id || l.source) !== id && (l.target.id || l.target) !== id);
        this.updateView();
        const ed = $('#nodeEditor');
        if (ed) ed.hidden = true;
        alert('已归档');
      } catch (err) {
        alert('归档失败: ' + err.message);
      }
    });

    // 提案回滚（/memory/{id}/rollback）：回退到上一版本
    $('#neRollbackBtn')?.addEventListener('click', async () => {
      const id = $('#neId')?.value;
      if (!id) return;
      if (!confirm('回滚将把该记忆恢复到上一版本（版本历史仍留痕）。确认回滚？')) return;
      try {
        await api(`/memory/${id}/rollback`, {method: 'POST'});
        // 重新拉取该节点内容
        const fresh = await api(`/terminal/memory/${id}`);
        const node = this.nodes.find(n => n.id === id);
        const content = fresh?.content ?? fresh?.memory?.content;
        if (node && content) { node.content = content; this.updateView(); }
        const ed = $('#neContent');
        if (ed && content) ed.value = content;
        alert('已回滚到上一版本');
      } catch (err) {
        alert('回滚失败: ' + err.message);
      }
    });
  }

  setData(nodes, edges) {
    this.nodes = nodes.map(d => ({...d}));
    this.links = edges.map(d => ({...d}));
    this.updateView();
  }

  addOrUpdateNode(nodeData) {
    const existing = this.nodes.find(n => n.id === nodeData.id);
    if (existing) {
      Object.assign(existing, nodeData);
    } else {
      this.nodes.push({...nodeData});
    }
    this.scheduleUpdate();
  }

  addOrUpdateEdge(edgeData) {
    const existing = this.links.find(l => {
      const s = l.source.id || l.source;
      const t = l.target.id || l.target;
      return (s === edgeData.source && t === edgeData.target);
    });
    if (!existing) {
      this.links.push({...edgeData});
      this.scheduleUpdate();
    }
  }

  // SSE 高频事件批量合并：一帧内多次 add 只触发一次重绘 + 力导重启
  scheduleUpdate() {
    if (this._updateQueued) return;
    this._updateQueued = true;
    requestAnimationFrame(() => {
      this._updateQueued = false;
      this.updateView();
    });
  }

  highlightNode(id) {
    this.nodeGroup.selectAll('.graph-node')
      .classed('active', d => d.id === id)
      .classed('dimmed', d => d.id !== id);
  }

  highlightEdges(edges) {
    const activeSources = new Set(edges.map(e => e.source));
    const activeTargets = new Set(edges.map(e => e.target));
    this.linkGroup.selectAll('.graph-link')
      .classed('active', d => {
        const s = d.source.id || d.source;
        const t = d.target.id || d.target;
        return activeSources.has(s) && activeTargets.has(t);
      })
      .classed('dimmed', d => {
        const s = d.source.id || d.source;
        const t = d.target.id || d.target;
        return !(activeSources.has(s) && activeTargets.has(t));
      });
  }

  clearHighlights() {
    this.nodeGroup.selectAll('.graph-node').classed('active', false).classed('dimmed', false);
    this.linkGroup.selectAll('.graph-link').classed('active', false).classed('dimmed', false);
  }

  updateView() {
    const el = document.querySelector('#graphCanvas');
    if (el) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        this.width = rect.width;
        this.height = rect.height;
        this.simulation.force("center", d3.forceCenter(this.width / 2, this.height / 2));
      }
    }

    // Links
    this.linkElements = this.linkGroup.selectAll("line")
      .data(this.links, d => d.id || `${d.source?.id || d.source}-${d.target?.id || d.target}`);
      
    this.linkElements.exit().remove();
    
    const linkEnter = this.linkElements.enter().append("line")
      .attr("class", "graph-link");
      
    this.linkElements = linkEnter.merge(this.linkElements);

    // Nodes
    this.nodeElements = this.nodeGroup.selectAll(".graph-node")
      .data(this.nodes, d => d.id);
      
    this.nodeElements.exit().remove();
    
    const nodeEnter = this.nodeElements.enter().append("g")
      .attr("class", "graph-node")
      .call(d3.drag()
        .on("start", this.dragstarted.bind(this))
        .on("drag", this.dragged.bind(this))
        .on("end", this.dragended.bind(this)))
      .on("click", (e, d) => {
        // 拖拽结束后紧跟的 click 是误触（弹编辑器/误触 link/merge）
        if (this._suppressClick) {
          this._suppressClick = false;
          return;
        }
        this.handleNodeClick(d);
      });
      
    nodeEnter.append("circle")
      .attr("r", d => 10 + (d.importance || 0) * 10);
      
    nodeEnter.append("text")
      .attr("dx", 15)
      .attr("dy", 4)
      .text(d => (d.content || '').substring(0, 10) + '...');
      
    this.nodeElements = nodeEnter.merge(this.nodeElements);

    // merge 后同步刷新半径与标签（enter 只在新建时设置，编辑/合并后会变陈）
    this.nodeElements.select("circle")
      .attr("r", d => 10 + (d.importance || 0) * 10);
    this.nodeElements.select("text")
      .text(d => (d.content || '').substring(0, 10) + '...');

    // Update Simulation
    this.simulation.nodes(this.nodes).on("tick", this.ticked.bind(this));
    this.simulation.force("link").links(this.links);
    this.simulation.alpha(0.8).restart();
  }

  ticked() {
    this.linkElements
      .attr("x1", d => d.source.x)
      .attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x)
      .attr("y2", d => d.target.y);

    this.nodeElements
      .attr("transform", d => `translate(${d.x},${d.y})`);
  }

  dragstarted(event, d) {
    if (!event.active) this.simulation.alphaTarget(0.3).restart();
    this._dragMoved = false;
    d.fx = d.x;
    d.fy = d.y;
  }
  
  dragged(event, d) {
    this._dragMoved = true;
    d.fx = event.x;
    d.fy = event.y;
  }
  
  dragended(event, d) {
    if (!event.active) this.simulation.alphaTarget(0);
    if (this._dragMoved) this._suppressClick = true;
    this._dragMoved = false;
    d.fx = null;
    d.fy = null;
  }

  async handleNodeClick(d) {
    if (this.mode === 'link') {
      if (this.selectedNodeId && this.selectedNodeId !== d.id) {
        await this.createEdge(this.selectedNodeId, d.id);
      }
      this.mode = 'view';
      const title = $('#nodeEditorTitle');
      if (title) title.textContent = '节点详情';
      return;
    }
    
    if (this.mode === 'merge') {
      if (this.selectedNodeId && this.selectedNodeId !== d.id) {
        await this.mergeNodes(this.selectedNodeId, d.id);
      }
      this.mode = 'view';
      const title = $('#nodeEditorTitle');
      if (title) title.textContent = '节点详情';
      const ed = $('#nodeEditor');
      if (ed) ed.hidden = true;
      return;
    }

    this.highlightNode(d.id);
    const ed = $('#nodeEditor');
    if (ed) ed.hidden = false;
    if ($('#neId')) $('#neId').value = d.id;
    if ($('#neContent')) $('#neContent').value = d.content || '';
    if ($('#neImportance')) $('#neImportance').value = d.importance || 0.8;
    if ($('#neConfidence')) $('#neConfidence').value = d.confidence || 0.9;
    if ($('#neType')) $('#neType').value = d.memory_type || 'semantic';
  }

  async createEdge(sourceId, targetId) {
    try {
      await api('/edges', {
        method: 'POST',
        body: JSON.stringify({ source_memory_id: sourceId, target_memory_id: targetId, relation: 'related', confidence: 1.0 })
      });
      alert('关联成功');
      this.addOrUpdateEdge({ source: sourceId, target: targetId, relation: 'related', confidence: 1.0 });
    } catch (e) {
      alert('关联失败: ' + e.message);
    }
  }

  async mergeNodes(sourceId, targetId) {
    if (!confirm(`确定要将内容合并入节点并删除源节点吗？`)) return;
    try {
      const res = await api('/terminal/merge', {
        method: 'POST',
        body: JSON.stringify({ source_id: sourceId, target_id: targetId })
      });
      alert('合并成功');
      this.nodes = this.nodes.filter(n => n.id !== sourceId);
      this.links = this.links.filter(l => (l.source.id || l.source) !== sourceId && (l.target.id || l.target) !== sourceId);
      const targetNode = this.nodes.find(n => n.id === targetId);
      if (targetNode) targetNode.content = res.content;
      this.updateView();
    } catch (e) {
      alert('合并失败: ' + e.message);
    }
  }
}
