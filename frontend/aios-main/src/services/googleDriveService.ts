/**
 * Google Drive API v3 Service for exporting project codebase to ~/code/frontend
 */

export interface DriveFileItem {
  id: string;
  name: string;
  mimeType: string;
  webViewLink?: string;
  parents?: string[];
}

export interface UploadProgress {
  current: number;
  total: number;
  currentFile: string;
  status: 'idle' | 'preparing' | 'creating_folders' | 'uploading' | 'completed' | 'error';
  message: string;
  driveFolderId?: string;
  driveFolderUrl?: string;
  uploadedFiles: { path: string; id: string; url?: string }[];
  error?: string;
}

const DRIVE_API_BASE = 'https://www.googleapis.com/drive/v3';
const DRIVE_UPLOAD_BASE = 'https://www.googleapis.com/upload/drive/v3';

// Get MIME type by file extension
export function getMimeType(fileName: string): string {
  if (fileName.endsWith('.ts') || fileName.endsWith('.tsx')) return 'text/typescript';
  if (fileName.endsWith('.js') || fileName.endsWith('.jsx')) return 'application/javascript';
  if (fileName.endsWith('.json')) return 'application/json';
  if (fileName.endsWith('.html')) return 'text/html';
  if (fileName.endsWith('.css')) return 'text/css';
  if (fileName.endsWith('.md')) return 'text/markdown';
  if (fileName.endsWith('.svg')) return 'image/svg+xml';
  if (fileName.endsWith('.txt') || fileName.endsWith('.example')) return 'text/plain';
  return 'text/plain';
}

/**
 * Find a folder by name in a parent folder, or create it if not found
 */
export async function findOrCreateFolder(
  accessToken: string,
  folderName: string,
  parentId: string = 'root'
): Promise<DriveFileItem> {
  const query = `mimeType = 'application/vnd.google-apps.folder' and name = '${folderName.replace(/'/g, "\\'")}' and '${parentId}' in parents and trashed = false`;
  const searchUrl = `${DRIVE_API_BASE}/files?q=${encodeURIComponent(query)}&fields=files(id,name,mimeType,webViewLink)&spaces=drive`;

  const searchRes = await fetch(searchUrl, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
  });

  if (!searchRes.ok) {
    const errText = await searchRes.text();
    throw new Error(`Google Drive API query error: ${searchRes.status} ${errText}`);
  }

  const searchData = await searchRes.json();
  if (searchData.files && searchData.files.length > 0) {
    return searchData.files[0];
  }

  // Folder doesn't exist, create it
  const createRes = await fetch(`${DRIVE_API_BASE}/files?fields=id,name,mimeType,webViewLink`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      name: folderName,
      mimeType: 'application/vnd.google-apps.folder',
      parents: [parentId],
    }),
  });

  if (!createRes.ok) {
    const errText = await createRes.text();
    throw new Error(`Failed to create Google Drive folder '${folderName}': ${errText}`);
  }

  return await createRes.json();
}

/**
 * Create path hierarchy (e.g., ['code', 'frontend', 'src', 'components']) in Google Drive
 */
export async function ensureFolderPath(
  accessToken: string,
  pathSegments: string[],
  rootFolderId: string = 'root'
): Promise<DriveFileItem> {
  let currentParentId = rootFolderId;
  let lastFolder: DriveFileItem = { id: rootFolderId, name: 'root', mimeType: 'application/vnd.google-apps.folder' };

  for (const segment of pathSegments) {
    if (!segment.trim()) continue;
    lastFolder = await findOrCreateFolder(accessToken, segment, currentParentId);
    currentParentId = lastFolder.id;
  }

  return lastFolder;
}

/**
 * Upload or overwrite a single file in Google Drive
 */
export async function uploadFileToDrive(
  accessToken: string,
  fileName: string,
  content: string,
  parentFolderId: string
): Promise<DriveFileItem> {
  const mimeType = getMimeType(fileName);

  // Check if file already exists in the parent folder
  const query = `name = '${fileName.replace(/'/g, "\\'")}' and '${parentFolderId}' in parents and trashed = false`;
  const searchUrl = `${DRIVE_API_BASE}/files?q=${encodeURIComponent(query)}&fields=files(id,name,mimeType,webViewLink)&spaces=drive`;

  const searchRes = await fetch(searchUrl, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  let existingFileId: string | null = null;
  if (searchRes.ok) {
    const searchData = await searchRes.json();
    if (searchData.files && searchData.files.length > 0) {
      existingFileId = searchData.files[0].id;
    }
  }

  const metadata = {
    name: fileName,
    mimeType: mimeType,
    ...(existingFileId ? {} : { parents: [parentFolderId] }),
  };

  const boundary = '-------314159265358979323846';
  const delimiter = `\r\n--${boundary}\r\n`;
  const closeDelimiter = `\r\n--${boundary}--`;

  const multipartRequestBody =
    delimiter +
    'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
    JSON.stringify(metadata) +
    delimiter +
    `Content-Type: ${mimeType}; charset=UTF-8\r\n\r\n` +
    content +
    closeDelimiter;

  let uploadUrl: string;
  let method: string;

  if (existingFileId) {
    // Update existing file
    uploadUrl = `${DRIVE_UPLOAD_BASE}/files/${existingFileId}?uploadType=multipart&fields=id,name,mimeType,webViewLink`;
    method = 'PATCH';
  } else {
    // Create new file
    uploadUrl = `${DRIVE_UPLOAD_BASE}/files?uploadType=multipart&fields=id,name,mimeType,webViewLink`;
    method = 'POST';
  }

  const uploadRes = await fetch(uploadUrl, {
    method,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': `multipart/related; boundary=${boundary}`,
    },
    body: multipartRequestBody,
  });

  if (!uploadRes.ok) {
    const errText = await uploadRes.text();
    throw new Error(`Failed to upload file '${fileName}' to Google Drive: ${errText}`);
  }

  return await uploadRes.json();
}

/**
 * Gather all app source code files using Vite dynamic raw imports
 */
export function getAppCodebaseFiles(): Record<string, string> {
  const files: Record<string, string> = {};

  try {
    // Import all src files as raw text
    const srcFiles = (import.meta as any).glob(['/src/**/*', '/src/**/.*'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;

    for (const [path, content] of Object.entries(srcFiles)) {
      const cleanPath = path.startsWith('/') ? path.slice(1) : path;
      if (typeof content === 'string' && content.trim().length > 0) {
        files[cleanPath] = content;
      }
    }
  } catch (e) {
    console.warn('Error loading raw src glob:', e);
  }

  try {
    // Import root config files as raw text
    const rootFiles = (import.meta as any).glob(
      ['/package.json', '/index.html', '/vite.config.ts', '/tsconfig.json', '/metadata.json', '/.env.example', '/.gitignore'],
      {
        query: '?raw',
        import: 'default',
        eager: true,
      }
    ) as Record<string, string>;

    for (const [path, content] of Object.entries(rootFiles)) {
      const cleanPath = path.startsWith('/') ? path.slice(1) : path;
      if (typeof content === 'string' && content.trim().length > 0) {
        files[cleanPath] = content;
      }
    }
  } catch (e) {
    console.warn('Error loading raw root files glob:', e);
  }

  // Add README.md for the exported project
  files['README.md'] = `# AI Studio - Full-Stack Agile & Task Orchestrator App

This is the complete exported codebase of the application, saved directly to Google Drive under \`code/frontend\`.

## 📦 Project Overview
- **Framework**: React 19 + TypeScript + Vite 6 + Tailwind CSS
- **Features**: 
  - Kanban Agile Board with Epics & Subtasks
  - Task Dependency DAG Flowchart
  - Multi-Agent Orchestration & Chat
  - Code Workspace & IDE Viewer
  - Gitea PRD Markdown Requirements & Annotation System
  - Google Drive Code Export & Sync
  - GitHub Light / Dark Theme Modes

## 🚀 Running Locally
\`\`\`bash
npm install
npm run dev
\`\`\`

Exported via Google Drive API Integration.
`;

  return files;
}

/**
 * Save all codebase files to Google Drive under ~/code/frontend
 */
export async function exportCodebaseToGoogleDrive(
  accessToken: string,
  onProgress?: (progress: UploadProgress) => void
): Promise<{ driveFolderUrl: string; totalFiles: number }> {
  const filesMap = getAppCodebaseFiles();
  const filePaths = Object.keys(filesMap);
  const totalFiles = filePaths.length;

  const progressState: UploadProgress = {
    current: 0,
    total: totalFiles,
    currentFile: '',
    status: 'preparing',
    message: '正在准备项目源码文件...',
    uploadedFiles: [],
  };

  onProgress?.(progressState);

  // 1. Ensure root destination folder ~/code/frontend exists in Google Drive
  progressState.status = 'creating_folders';
  progressState.message = '正在 Google Drive 中创建/定位 ~/code/frontend 目标目录...';
  onProgress?.({ ...progressState });

  const codeFolder = await findOrCreateFolder(accessToken, 'code', 'root');
  const frontendFolder = await findOrCreateFolder(accessToken, 'frontend', codeFolder.id);

  const driveFolderId = frontendFolder.id;
  const driveFolderUrl = frontendFolder.webViewLink || `https://drive.google.com/drive/folders/${driveFolderId}`;
  progressState.driveFolderId = driveFolderId;
  progressState.driveFolderUrl = driveFolderUrl;

  // Cache folder IDs by relative directory path
  const folderCache: Record<string, string> = {
    '': frontendFolder.id,
    '.': frontendFolder.id,
  };

  async function getFolderIdForPath(relDirPath: string): Promise<string> {
    if (folderCache[relDirPath]) {
      return folderCache[relDirPath];
    }

    const segments = relDirPath.split('/').filter(Boolean);
    let currentParentId = frontendFolder.id;
    let accumulatedPath = '';

    for (const seg of segments) {
      accumulatedPath = accumulatedPath ? `${accumulatedPath}/${seg}` : seg;
      if (folderCache[accumulatedPath]) {
        currentParentId = folderCache[accumulatedPath];
      } else {
        const folder = await findOrCreateFolder(accessToken, seg, currentParentId);
        folderCache[accumulatedPath] = folder.id;
        currentParentId = folder.id;
      }
    }

    return currentParentId;
  }

  // 2. Upload each file sequentially to avoid rate limits
  progressState.status = 'uploading';
  onProgress?.({ ...progressState });

  for (let i = 0; i < filePaths.length; i++) {
    const filePath = filePaths[i];
    const content = filesMap[filePath];

    const lastSlash = filePath.lastIndexOf('/');
    const dirPath = lastSlash !== -1 ? filePath.substring(0, lastSlash) : '';
    const fileName = lastSlash !== -1 ? filePath.substring(lastSlash + 1) : filePath;

    progressState.current = i + 1;
    progressState.currentFile = filePath;
    progressState.message = `正在上传 (${i + 1}/${totalFiles}): ${filePath}...`;
    onProgress?.({ ...progressState });

    try {
      const targetFolderId = await getFolderIdForPath(dirPath);
      const uploadedItem = await uploadFileToDrive(accessToken, fileName, content, targetFolderId);

      progressState.uploadedFiles.push({
        path: filePath,
        id: uploadedItem.id,
        url: uploadedItem.webViewLink,
      });
      onProgress?.({ ...progressState });
    } catch (err: any) {
      console.error(`Error uploading ${filePath}:`, err);
      // continue uploading remaining files
    }
  }

  progressState.status = 'completed';
  progressState.message = `🎉 已成功将全量 ${progressState.uploadedFiles.length} 个源码文件保存至 Google Drive: ~/code/frontend！`;
  onProgress?.({ ...progressState });

  return {
    driveFolderUrl,
    totalFiles: progressState.uploadedFiles.length,
  };
}

