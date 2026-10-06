import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// 获取当前文件的目录路径
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 源目录和目标目录
const sourceDir = path.join(__dirname, '..', 'out');
const targetDir = path.join(__dirname, '..', '..', 'src', 'main', 'resources', 'dist');

console.log('开始复制前端构建文件...');
console.log('源目录:', sourceDir);
console.log('目标目录:', targetDir);

// 递归删除目录
function deleteFolderRecursive(dirPath) {
  if (fs.existsSync(dirPath)) {
    fs.readdirSync(dirPath).forEach((file) => {
      const curPath = path.join(dirPath, file);
      if (fs.lstatSync(curPath).isDirectory()) {
        deleteFolderRecursive(curPath);
      } else {
        fs.unlinkSync(curPath);
      }
    });
    fs.rmdirSync(dirPath);
  }
}

// 递归复制目录
function copyFolderRecursive(source, target) {
  // 创建目标目录
  if (!fs.existsSync(target)) {
    fs.mkdirSync(target, { recursive: true });
  }

  // 读取源目录
  const files = fs.readdirSync(source);

  files.forEach((file) => {
    const sourcePath = path.join(source, file);
    const targetPath = path.join(target, file);

    if (fs.lstatSync(sourcePath).isDirectory()) {
      // 递归复制子目录
      copyFolderRecursive(sourcePath, targetPath);
    } else {
      // 复制文件
      fs.copyFileSync(sourcePath, targetPath);
    }
  });
}

/**
 * 补齐 RSC 预取载荷的点号形式别名。
 *
 * Next 16.3.x 在 output: 'export' 模式下，把嵌套路由的分段预取载荷导出成目录形式
 * （boss/__next.boss/__PAGE__.txt），但它的客户端运行时请求的是点号形式
 * （/boss/__next.boss.__PAGE__.txt），两者不一致，导致每次带 <Link> 的页面加载都
 * 有若干 404，客户端软导航的预取失效、退化成整页刷新。根路由的
 * __next.__PAGE__.txt 是点号形式，所以只有嵌套路由受影响。
 *
 * 这里保留原目录结构不动，额外按客户端期望的名字写一份同内容的别名文件：
 *   boss/__next.boss/__PAGE__.txt          -> boss/__next.boss.__PAGE__.txt
 *   boss/analysis/__next.boss/analysis/... -> boss/analysis/__next.boss.analysis.__PAGE__.txt
 * 等 Next 修好这个导出 bug 后，此函数可以整段删除。
 */
function createSegmentPrefetchAliases(root) {
  let created = 0;

  // 收集 <parent>/__next.<seg> 形式的目录，并把目录内剩余路径用点号拼到目录名后面
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (!entry.isDirectory()) continue;

      if (entry.name.startsWith('__next.')) {
        created += flatten(full, dir, entry.name);
      } else {
        walk(full);
      }
    }
  }

  // 把 segDir 内的所有文件，按 <prefix>.<相对路径用点连接> 写到 parent 下
  function flatten(segDir, parent, prefix) {
    let n = 0;
    for (const entry of fs.readdirSync(segDir, { withFileTypes: true })) {
      const full = path.join(segDir, entry.name);
      if (entry.isDirectory()) {
        n += flatten(full, parent, `${prefix}.${entry.name}`);
        continue;
      }
      const aliasPath = path.join(parent, `${prefix}.${entry.name}`);
      if (!fs.existsSync(aliasPath)) {
        fs.copyFileSync(full, aliasPath);
        n++;
      }
    }
    return n;
  }

  walk(root);
  return created;
}

try {
  // 检查源目录是否存在
  if (!fs.existsSync(sourceDir)) {
    console.error('错误: out目录不存在，请先运行 pnpm run build');
    process.exit(1);
  }

  // 删除旧的目标目录
  if (fs.existsSync(targetDir)) {
    console.log('删除旧的目标目录...');
    deleteFolderRecursive(targetDir);
  }

  // 复制文件
  console.log('复制文件...');
  copyFolderRecursive(sourceDir, targetDir);

  // 补齐 RSC 预取载荷的点号形式别名
  const aliasCount = createSegmentPrefetchAliases(targetDir);
  if (aliasCount > 0) {
    console.log(`已补齐 ${aliasCount} 个 RSC 预取别名文件`);
  }

  console.log('✅ 构建文件复制成功!');
  console.log(`文件已复制到: ${targetDir}`);
} catch (error) {
  console.error('❌ 复制失败:', error.message);
  process.exit(1);
}
