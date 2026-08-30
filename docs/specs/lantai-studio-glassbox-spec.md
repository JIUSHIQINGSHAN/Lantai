# Technical Specification: Lantai Glass-box Cognitive Terminal

## 1. Overview & Goals
**Problem**: The memory system acts as a "black box". The user knows memories exist but cannot see how they are retrieved during a chat, nor can they explicitly build and manage the underlying knowledge graph visually.
**Goal**: Transform Lantai Studio into a "Glass-box" Interactive Cognitive Terminal.
- Combine a Chat Interface with a Real-time Memory X-Ray (Split-screen).
- Provide Deep Graph Editing (Obsidian-style) for manual CRUD, bi-directional linking, node merging, and relationship building.
- Strictly Local-first architecture without heavy network dependencies.

**Non-goals**:
- No distributed cloud server deployment.
- No heavy WebSocket architecture if HTTP streaming/polling suffices for local UX.

## 2. Architecture & Data Flow
**Core Layout**:
- **Left Pane (Chat Terminal)**: Conversational UI interacting with the local AI model.
- **Right Pane (Memory X-Ray & Graph Inspector)**: Real-time visualization of activated memory nodes.

**Data Flow (Local)**:
1. User sends message -> Local Chat API.
2. Chat API executes internal steps (Query Expansion -> Vector Search -> BM25 -> Re-ranking).
3. Instead of waiting for the full LLM generation, the backend streams these intermediate memory retrieval states to the frontend via Server-Sent Events (SSE) or simple fast polling.
4. The Right Pane (Graph Inspector) animates the newly retrieved memory nodes and highlights connections.

**Graph Data Model**:
- Entities (Nodes): User, Project, Concept.
- Edges: Relationships established by the LLM or manually drawn by the user.
- Storage: Local SQLite / FTS5 + Vector DB.

## 3. Detailed Component Design
### 3.1 Split-Screen Terminal UI
- **DOM Structure**: A resizable grid layout splitting the viewport `50vw / 50vw`.
- **Chat Feed**: Standard message bubbles, utilizing `marked` for markdown rendering.
- **X-Ray Visualizer**: A 2D network graph (using a lightweight canvas/SVG library like D3.js or Vis.js) rendering active memory nodes.

### 3.2 Deep Graph Editor (CRUD)
- Users can click on a node in the right pane to open a "Node Details" inspector.
- **Merge Nodes**: Dragging node A onto node B triggers a merge conflict resolution modal.
- **Link Nodes**: Drawing an edge between two nodes creates a new semantic relationship in the DB.
- **Edit Content**: Double-clicking a memory chunk allows direct editing of the raw text and importance score.

### 3.3 Backend Telemetry Streaming (SSE)
- Create a new FastAPI route `GET /api/chat/stream`.
- Yield JSON payloads with `event_type`: `[retrieval_start, nodes_activated, llm_generation, consolidation_trigger]`.

## 4. Security, Performance & Scalability
- **Performance**: The graph visualizer must cull nodes out of viewport. Do not render 10,000 nodes; only render the "Active Working Memory Neighborhood" (max ~150 nodes).
- **Scalability**: FTS5 and Vector search must respond in < 150ms locally to maintain the "real-time X-Ray" illusion.
