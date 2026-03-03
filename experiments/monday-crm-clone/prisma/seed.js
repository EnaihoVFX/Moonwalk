const { PrismaClient } = require('@prisma/client');
const prisma = new PrismaClient();

async function main() {
  // Clean up existing data
  await prisma.columnValue.deleteMany();
  await prisma.item.deleteMany();
  await prisma.group.deleteMany();
  await prisma.column.deleteMany();
  await prisma.automation.deleteMany();
  await prisma.board.deleteMany();
  await prisma.workspace.deleteMany();
  await prisma.user.deleteMany();

  // Create a user
  const user = await prisma.user.create({
    data: {
      email: 'admin@example.com',
      name: 'Admin User',
      password: 'hashed_password_here', // In a real app, this would be hashed
    },
  });

  // Create a workspace
  const workspace = await prisma.workspace.create({
    data: {
      name: 'Main Workspace',
      description: 'The primary workspace for our CRM',
      ownerId: user.id,
      members: {
        connect: { id: user.id },
      },
    },
  });

  // Create a board
  const board = await prisma.board.create({
    data: {
      name: 'Sales Pipeline',
      description: 'Track our sales leads and deals',
      workspaceId: workspace.id,
    },
  });

  // Create columns
  const statusColumn = await prisma.column.create({
    data: {
      title: 'Status',
      type: 'status',
      boardId: board.id,
      position: 1,
      settings: JSON.stringify({
        labels: {
          'Working on it': '#fdab3d',
          'Stuck': '#e2445c',
          'Done': '#00c875',
          'Default': '#c4c4c4'
        }
      }),
    },
  });

  const dateColumn = await prisma.column.create({
    data: {
      title: 'Timeline',
      type: 'date',
      boardId: board.id,
      position: 2,
    },
  });

  const priorityColumn = await prisma.column.create({
    data: {
      title: 'Priority',
      type: 'status',
      boardId: board.id,
      position: 3,
      settings: JSON.stringify({
        labels: {
          'High': '#e2445c',
          'Medium': '#fdab3d',
          'Low': '#579bfc',
          'Default': '#c4c4c4'
        }
      }),
    },
  });

  // Create groups
  const group1 = await prisma.group.create({
    data: {
      title: 'This Month',
      color: '#579bfc',
      boardId: board.id,
      position: 1,
    },
  });

  const group2 = await prisma.group.create({
    data: {
      title: 'Next Month',
      color: '#ffcb00',
      boardId: board.id,
      position: 2,
    },
  });

  // Create items and column values
  const items = [
    { name: 'Project Alpha', group: group1 },
    { name: 'Client Meeting', group: group1 },
    { name: 'Proposal Draft', group: group2 },
  ];

  for (const itemData of items) {
    const item = await prisma.item.create({
      data: {
        name: itemData.name,
        groupId: itemData.group.id,
        position: 1,
      },
    });

    // Add status value
    await prisma.columnValue.create({
      data: {
        itemId: item.id,
        columnId: statusColumn.id,
        value: 'Working on it',
      },
    });

    // Add priority value
    await prisma.columnValue.create({
      data: {
        itemId: item.id,
        columnId: priorityColumn.id,
        value: 'High',
      },
    });
  }

  console.log('Seed completed successfully!');
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
