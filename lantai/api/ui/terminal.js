import { api } from './api.js';

const $ = selector => document.querySelector(selector);
let graph = null; // 存储 graph 实例

export function initTerminal() {
  $('#terminalSendBtn').addEventListener('click', sendTerminalChat);
  $('#terminalInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') sendTerminalChat();
  });
  $('#graphLoadAllBtn').addEventListener('click', loadFullGraph);

  // 初始化图谱实例 (依赖于 D3.js)
  if (window.d3) {
    graph = new MemoryGraph('#graphCanvas');
  }
}

export function activateTerminalView() {
  if (graph && graph.nodes.length === 0) {
    loadFullGraph();
  }
}

function appendChatBubble(role, text) {
  const feed = $('#chatFeed');
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = text;
  feed.appendChild(bubble);
  feed.scrollTop = feed.scrollHeight;
}

async function sendTerminalChat() {
  const input = $('#terminalInput');
  const query = input.value.trim();
  if (!query) return;

  const domain = $('#terminalDomain').value;
  input.value = '';
  appendChatBubble('user', query);

  const btn = $('#terminalSendBtn');
  btn.disabled = true;

  try {
    // 使用 SSE 获取流式响应
    const key = sessionStorage.getItem('lantai-api-key-session') || localStorage.getItem('lantai_api_key') || '';
    
    // 发起 SSE 请求之前，先把之前的图谱高亮清除
    if (graph) graph.clearHighlights();
    
    const response = await fetch('/api/terminal/chat', {
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
      buffer = parts.pop(); // 最后一部分可能不完整

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
          const data = JSON.parse(dataStr);
          handleSseEvent(event, data);
        }
      }
    }
    
    appendChatBubble('agent', '已完成检索与拓扑链路分析。可以在右侧图谱观察唤醒的记忆。');

  } catch (err) {
    appendChatBubble('system', `检索失败: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function handleSseEvent(event, data) {
  if (event === 'step') {
    appendChatBubble('system', `[系统] ${data.message}`);
    $('#graphStatus').textContent = data.message;
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
    $('#graphStatus').textContent = data.message;
  } else if (event === 'error') {
    appendChatBubble('system', `[异常] ${data.message}`);
  }
}

async function loadFullGraph() {
  const btn = $('#graphLoadAllBtn');
  btn.disabled = true;
  $('#graphStatus').textContent = '加载全库拓扑...';
  
  try {
    const data = await api('/terminal/graph?limit=200');
    if (graph) {
      graph.setData(data.nodes, data.edges);
      $('#graphStatus').textContent = `全图谱 (节点: ${data.nodes.length})`;
    }
  } catch (err) {
    $('#graphStatus').textContent = '加载失败: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

/* --------------- 简单的 D3 图谱封装 --------------- */
class MemoryGraph {
  constructor(selector) {
    this.svg = d3.select(selector);
    this.width = this.svg.node().getBoundingClientRect().width;
    this.height = this.svg.node().getBoundingClientRect().height;
    
    this.nodes = [];
    this.links = [];
    this.mode = 'view'; // view, link, merge
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
    this.g = this.svg.append("g");
    
    // Zoom
    const zoom = d3.zoom()
      .scaleExtent([0.1, 4])
      .on("zoom", (e) => this.g.attr("transform", e.transform));
    this.svg.call(zoom);

    this.linkGroup = this.g.append("g").attr("class", "links");
    this.nodeGroup = this.g.append("g").attr("class", "nodes");
  }

  initEditor() {
    $('#nodeEditorClose').addEventListener('click', () => {
      $('#nodeEditor').hidden = true;
      this.mode = 'view';
      this.clearHighlights();
    });
    
    $('#neSaveBtn').addEventListener('click', async () => {
      const id = $('#neId').value;
      if (!id) return;
      try {
        await api(`/terminal/memory/${id}`, {
          method: 'PUT',
          body: JSON.stringify({
            content: $('#neContent').value,
            importance: parseFloat($('#neImportance').value),
            confidence: parseFloat($('#neConfidence').value),
            memory_type: $('#neType').value
          })
        });
        // 更新本地节点
        const node = this.nodes.find(n => n.id === id);
        if (node) {
          node.content = $('#neContent').value;
          this.updateView();
        }
        alert('保存成功');
      } catch (err) {
        alert('保存失败: ' + err.message);
      }
    });

    $('#neDeleteBtn').addEventListener('click', async () => {
      const id = $('#neId').value;
      if (!id) return;
      if (!confirm('确定删除此记忆？')) return;
      try {
        await api(`/terminal/memory/${id}`, { method: 'DELETE' });
        this.nodes = this.nodes.filter(n => n.id !== id);
        this.links = this.links.filter(l => l.source.id !== id && l.target.id !== id);
        this.updateView();
        $('#nodeEditor').hidden = true;
      } catch (err) {
        alert('删除失败: ' + err.message);
      }
    });

    $('#neLinkBtn').addEventListener('click', () => {
      this.mode = 'link';
      this.selectedNodeId = $('#neId').value;
      $('#nodeEditorTitle').textContent = '点击另一个节点建立关联...';
    });
    
    $('#neMergeBtn').addEventListener('click', () => {
      this.mode = 'merge';
      this.selectedNodeId = $('#neId').value;
      $('#nodeEditorTitle').textContent = '点击另一个节点合并...';
    });
  }

  setData(nodes, edges) {
    this.nodes = nodes.map(d => Object.create(d));
    this.links = edges.map(d => Object.create(d));
    this.updateView();
  }

  addOrUpdateNode(nodeData) {
    const existing = this.nodes.find(n => n.id === nodeData.id);
    if (existing) {
      Object.assign(existing, nodeData);
    } else {
      this.nodes.push(Object.create(nodeData));
    }
    this.updateView();
  }

  addOrUpdateEdge(edgeData) {
    const existing = this.links.find(l => 
      (l.source.id === edgeData.source && l.target.id === edgeData.target) ||
      (l.source === edgeData.source && l.target === edgeData.target)
    );
    if (!existing) {
      this.links.push(Object.create(edgeData));
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
      .classed('active', d => activeSources.has(d.source.id) && activeTargets.has(d.target.id))
      .classed('dimmed', d => !(activeSources.has(d.source.id) && activeTargets.has(d.target.id)));
  }

  clearHighlights() {
    this.nodeGroup.selectAll('.graph-node').classed('active', false).classed('dimmed', false);
    this.linkGroup.selectAll('.graph-link').classed('active', false).classed('dimmed', false);
  }

  updateView() {
    // Links
    this.linkElements = this.linkGroup.selectAll("line")
      .data(this.links, d => d.id || `${d.source}-${d.target}`);
      
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
    this.simulation.alpha(1).restart();
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
      $('#nodeEditorTitle').textContent = '节点详情';
      return;
    }
    
    if (this.mode === 'merge') {
      if (this.selectedNodeId && this.selectedNodeId !== d.id) {
        await this.mergeNodes(this.selectedNodeId, d.id);
      }
      this.mode = 'view';
      $('#nodeEditorTitle').textContent = '节点详情';
      $('#nodeEditor').hidden = true;
      return;
    }

    // Default mode: open inspector
    this.highlightNode(d.id);
    $('#nodeEditor').hidden = false;
    $('#neId').value = d.id;
    $('#neContent').value = d.content || '';
    $('#neImportance').value = d.importance || 0.8;
    $('#neConfidence').value = d.confidence || 0.9;
    $('#neType').value = d.memory_type || 'semantic';
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
      // 刷新节点
      this.nodes = this.nodes.filter(n => n.id !== sourceId);
      this.links = this.links.filter(l => l.source.id !== sourceId && l.target.id !== sourceId);
      const targetNode = this.nodes.find(n => n.id === targetId);
      if (targetNode) targetNode.content = res.content;
      this.updateView();
    } catch (e) {
      alert('合并失败: ' + e.message);
    }
  }
}
