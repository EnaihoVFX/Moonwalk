import React from 'react'
import Sidebar from './components/Sidebar'
import Board from './components/Board'

function App() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        {/* Workspace Header (Optional) */}
        <div className="h-12 border-b border-monday-border bg-white flex items-center px-4 text-sm font-medium">
          Workspace / My Project
        </div>
        <Board />
      </div>
    </div>
  )
}

export default App
