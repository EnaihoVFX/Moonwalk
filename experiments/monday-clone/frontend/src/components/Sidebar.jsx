import React from 'react';
import { Home, Layout, CheckSquare, Bell, Search, Plus, Settings } from 'lucide-react';

const Sidebar = () => {
  return (
    <div className="w-16 h-screen bg-[#2b2c32] flex flex-col items-center py-4 space-y-6 text-white">
      <div className="w-10 h-10 bg-monday-blue rounded-lg flex items-center justify-center mb-4">
        <span className="font-bold text-xl">M</span>
      </div>
      <div className="flex flex-col space-y-6 flex-1">
        <Bell size={24} className="cursor-pointer hover:text-monday-blue transition-colors" />
        <Layout size={24} className="cursor-pointer text-monday-blue" />
        <CheckSquare size={24} className="cursor-pointer hover:text-monday-blue transition-colors" />
        <Search size={24} className="cursor-pointer hover:text-monday-blue transition-colors" />
      </div>
      <div className="flex flex-col space-y-6">
        <Plus size={24} className="cursor-pointer hover:text-monday-blue transition-colors" />
        <Settings size={24} className="cursor-pointer hover:text-monday-blue transition-colors" />
        <div className="w-8 h-8 bg-purple-500 rounded-full flex items-center justify-center text-xs font-bold">
          JD
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
