import { api } from './api.js';

const $ = selector => document.querySelector(selector);
let graph = null;

export function initTerminal() {
  $('#terminalSendBtn')?.addEventListener('click', sendTerminalChat);
  $('#terminalInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') sendTerminalChat();
  });
  $('#graphLoadAllBtn')?.addEventListener('click', loadFullGraph);
}

export function activateTerminalView() {
  if (!graph && window.d3) {
    try {
      graph = new MemoryGraph('#graphCanvas');
    } catch (e) {
      console.warn('初始化图谱异常', e);
    }
  }
  if (graph && graph.nodes.length === 0) {
    loadFullGraph();
  }
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

  try {
    const key = sessionStorage.getItem('lantai-api-key-session') || localStorage.getItem('lantai_api_key') || '';
    
    if (graph) graph.clearHighlights();
    
    const response = await fetch('/terminal/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(key ? {'X-API-Key': key} : {})
      },
      body: JSON.stringify({ query, domain, force: true, top_k: 8 })
    });

    if (!response.ok) throw new Error('网络异常: ' + response.status);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();

      for (const part of parts) {
        if (!part.trim()) continue;
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

        if (dataStr) {
          try {
            const data = JSON.parse(dataStr);
            handleSseEvent(event, data);
          } catch (err) {
            console.error('SSE JSON 解析错误', err);
          }
        }
      }
    }
    
    appendChatBubble('agent', '已完成检索与拓扑链路分析。可以在右侧图谱观察唤醒的记忆。');

  } catch (err) {
    appendChatBubble('system', `检索失败: ${err.message}`);
  } finally {
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
    const data = await api('/terminal/graph?limit=200');
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
    const el = document.querySelector(selector);
    if (!el) return;
    this.svg = d3.select(selector);
    const rect = el.getBoundingClientRect();
    this.width = rect.width || 600;
    this.height = rect.height || 500;
    
    this.nodes = [];
    this.links = [];
    this.mode = 'view';
    this.selectedNodeId = null;

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
    this.updateView();
  }

  addOrUpdateEdge(edgeData) {
    const existing = this.links.find(l => {
      const s = l.source.id || l.source;
      const t = l.target.id || l.target;
      return (s === edgeData.source && t === edgeData.target);
    });
    if (!existing) {
      this.links.push({...edgeData});
      this.updateView();
    }
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
      .on("click", (e, d) => this.handleNodeClick(d));
      
    nodeEnter.append("circle")
      .attr("r", d => 10 + (d.importance || 0) * 10);
      
    nodeEnter.append("text")
      .attr("dx", 15)
      .attr("dy", 4)
      .text(d => (d.content || '').substring(0, 10) + '...');
      
    this.nodeElements = nodeEnter.merge(this.nodeElements);

    // Update Simulation
    this.simulation.nodes(this.nodes).on("tick", this.ticked.bind(this));
    this.simulation.force("link").links(this.links);
    this.simulation.alpha(0.3).restart();
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
    d.fx = d.x;
    d.fy = d.y;
  }
  
  dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }
  
  dragended(event, d) {
    if (!event.active) this.simulation.alphaTarget(0);
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
