import React, { useState, useEffect } from 'react';
import { Star, Info, UserPlus, Search, Filter, MoreHorizontal, Plus } from 'lucide-react';

const Board = () => {
  const [board, setBoard] = useState({
    name: 'Main Board',
    groups: [
      {
        id: '1',
        title: 'Group 1',
        color: '#579bfc',
        items: [{ id: 'i1', name: 'Item 1' }, { id: 'i2', name: 'Item 2' }]
      }
    ]
  });

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-white">
      {/* Board Header */}
      <div className="p-6 border-b border-monday-border">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-4">
            <h1 className="text-2xl font-bold">{board.name}</h1>
            <Star size={20} className="text-gray-400 cursor-pointer" />
            <Info size={20} className="text-gray-400 cursor-pointer" />
          </div>
          <div className="flex items-center space-x-3">
            <button className="flex items-center space-x-2 px-3 py-1.5 border border-monday-border rounded hover:bg-gray-50">
              <UserPlus size={16} />
              <span>Invite / 1</span>
            </button>
            <button className="p-1.5 border border-monday-border rounded hover:bg-gray-50">
              <MoreHorizontal size={16} />
            </button>
          </div>
        </div>
        
        <div className="flex items-center space-x-6 text-sm text-gray-600">
          <div className="flex items-center space-x-1 border-b-2 border-monday-blue pb-2 text-monday-blue font-medium cursor-pointer">
            <span>Main Table</span>
          </div>
          <div className="flex items-center space-x-1 pb-2 cursor-pointer hover:text-black">
            <Plus size={14} />
            <span>Add View</span>
          </div>
        </div>
      </div>

      {/* Board Toolbar */}
      <div className="px-6 py-3 flex items-center justify-between border-b border-monday-border">
        <div className="flex items-center space-x-3">
          <button className="bg-monday-blue text-white px-3 py-1.5 rounded text-sm font-medium hover:bg-blue-600">
            New Item
          </button>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input 
              type="text" 
              placeholder="Search" 
              className="pl-9 pr-3 py-1.5 border border-monday-border rounded text-sm focus:outline-none focus:border-monday-blue w-48"
            />
          </div>
          <button className="flex items-center space-x-2 px-3 py-1.5 hover:bg-gray-100 rounded text-sm">
            <Filter size={16} />
            <span>Filter</span>
          </button>
        </div>
      </div>

      {/* Board Content */}
      <div className="flex-1 overflow-auto p-6">
        {board.groups.map(group => (
          <div key={group.id} className="mb-8">
            <div className="flex items-center space-x-2 mb-2">
              <div className="w-1.5 h-6 rounded-sm" style={{ backgroundColor: group.color }}></div>
              <h3 className="font-medium text-lg" style={{ color: group.color }}>{group.title}</h3>
            </div>
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-y border-monday-border">
                  <th className="w-8 p-2"></th>
                  <th className="p-2 font-normal border-r border-monday-border">Item</th>
                  <th className="p-2 font-normal border-r border-monday-border w-32 text-center">Status</th>
                  <th className="p-2 font-normal border-r border-monday-border w-32 text-center">Date</th>
                  <th className="w-8 p-2"></th>
                </tr>
              </thead>
              <tbody>
                {group.items.map(item => (
                  <tr key={item.id} className="border-b border-monday-border hover:bg-gray-50 group">
                    <td className="p-2 text-center">
                      <input type="checkbox" className="rounded border-gray-300" />
                    </td>
                    <td className="p-2 border-r border-monday-border">{item.name}</td>
                    <td className="p-2 border-r border-monday-border">
                      <div className="bg-gray-200 text-white py-1 text-center rounded-sm">Working on it</div>
                    </td>
                    <td className="p-2 border-r border-monday-border text-center text-gray-500">-</td>
                    <td className="p-2 opacity-0 group-hover:opacity-100">
                      <Plus size={14} className="cursor-pointer" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Board;
