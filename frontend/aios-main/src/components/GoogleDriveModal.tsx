import React, { useState, useEffect } from 'react';
import {
  FolderGit2,
  HardDrive,
  Cloud,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  RefreshCw,
  X,
  FileCode,
  Folder,
  ChevronRight,
  ShieldCheck,
  LogOut,
  Sparkles,
  ArrowRight,
} from 'lucide-react';
import { User } from 'firebase/auth';
import {
  initAuth,
  googleSignIn,
  getAccessToken,
  logout,
} from '../services/googleDriveAuth';
import {
  exportCodebaseToGoogleDrive,
  getAppCodebaseFiles,
  UploadProgress,
} from '../services/googleDriveService';

interface GoogleDriveModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const GoogleDriveModal: React.FC<GoogleDriveModalProps> = ({
  isOpen,
  onClose,
}) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Export State
  const [showConfirmStep, setShowConfirmStep] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [allFiles, setAllFiles] = useState<Record<string, string>>({});

  useEffect(() => {
    if (isOpen) {
      const files = getAppCodebaseFiles();
      setAllFiles(files);
    }
  }, [isOpen]);

  useEffect(() => {
    const unsubscribe = initAuth(
      (authUser, authToken) => {
        setUser(authUser);
        setToken(authToken);
        setAuthError(null);
      },
      () => {
        setUser(null);
        setToken(null);
      }
    );
    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, []);

  const handleSignIn = async () => {
    setIsLoggingIn(true);
    setAuthError(null);
    try {
      const result = await googleSignIn();
      if (result) {
        setUser(result.user);
        setToken(result.accessToken);
      }
    } catch (err: any) {
      console.error('Google Sign In failed:', err);
      setAuthError(err.message || 'Google 账号授权失败，请重试');
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
      setUser(null);
      setToken(null);
      setProgress(null);
      setShowConfirmStep(false);
    } catch (err: any) {
      console.error('Logout failed:', err);
    }
  };

  const startExport = async () => {
    if (!token) {
      setAuthError('请先使用 Google 账号登录并授权 Google Drive 访问权限');
      return;
    }

    setIsExporting(true);
    setShowConfirmStep(false);
    setAuthError(null);

    try {
      const result = await exportCodebaseToGoogleDrive(token, (prog) => {
        setProgress(prog);
      });
      console.log('Google Drive export completed successfully:', result);
    } catch (err: any) {
      console.error('Export to Google Drive failed:', err);
      setAuthError(err.message || '上传至 Google Drive 发生错误');
      setProgress((prev) =>
        prev
          ? { ...prev, status: 'error', error: err.message || '上传中断' }
          : null
      );
    } finally {
      setIsExporting(false);
    }
  };

  if (!isOpen) return null;

  const fileCount = Object.keys(allFiles).length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-[#18191b] border border-[#333538] w-full max-w-2xl rounded-2xl shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[#333538] flex items-center justify-between bg-[#1e1f20]">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
              <HardDrive className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h2 className="text-base font-bold text-white">Google Drive 源码云端同步</h2>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 font-mono">
                  ~/code/frontend
                </span>
              </div>
              <p className="text-xs text-zinc-400 mt-0.5">
                将本应用前端项目全量代码无缝同步至您的个人 Google Drive 云端硬盘
              </p>
            </div>
          </div>
          <button
            id="btn-close-drive-modal"
            onClick={onClose}
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-[#282a2c] rounded-lg transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body Content */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1">
          {/* Target Location Banner */}
          <div className="bg-[#131314] p-4 rounded-xl border border-[#333538] flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-lg bg-[#1e1f20] border border-[#333538] flex items-center justify-center text-zinc-300">
                <Folder className="w-4 h-4 text-amber-400" />
              </div>
              <div>
                <div className="text-[11px] text-zinc-400 font-medium">目标云端存储路径</div>
                <div className="text-xs font-mono font-bold text-zinc-200 flex items-center space-x-1 mt-0.5">
                  <span className="text-blue-400">My Drive</span>
                  <ChevronRight className="w-3 h-3 text-zinc-600" />
                  <span className="text-amber-300">code</span>
                  <ChevronRight className="w-3 h-3 text-zinc-600" />
                  <span className="text-emerald-400">frontend</span>
                </div>
              </div>
            </div>

            <div className="text-right">
              <div className="text-[10px] text-zinc-500">待保存文件总量</div>
              <div className="text-xs font-mono font-bold text-white">{fileCount} 个源文件</div>
            </div>
          </div>

          {/* Auth State Section */}
          {!user ? (
            <div className="bg-[#1e1f20] p-5 rounded-xl border border-[#333538] text-center space-y-4">
              <div className="w-12 h-12 mx-auto rounded-full bg-[#131314] border border-[#333538] flex items-center justify-center">
                <Cloud className="w-6 h-6 text-blue-400" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-white">连接您的 Google 账号</h3>
                <p className="text-xs text-zinc-400 mt-1 max-w-md mx-auto">
                  点击下方按钮完成 Google 账号授权，系统将获得将代码写入您 Google Drive 目录下的专用权限。
                </p>
              </div>

              {authError && (
                <div className="p-3 bg-red-950/50 border border-red-800 rounded-lg text-xs text-red-300 flex items-center space-x-2 text-left">
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{authError}</span>
                </div>
              )}

              {/* Official Google Sign In Button */}
              <div className="flex justify-center pt-2">
                <button
                  id="btn-google-drive-sign-in"
                  onClick={handleSignIn}
                  disabled={isLoggingIn}
                  className="flex items-center space-x-3 px-5 py-2.5 bg-white hover:bg-zinc-100 text-zinc-900 rounded-lg font-medium text-xs shadow-md transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed border border-zinc-300"
                >
                  <svg
                    className="w-4 h-4"
                    viewBox="0 0 24 24"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                      fill="#4285F4"
                    />
                    <path
                      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                      fill="#34A853"
                    />
                    <path
                      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                      fill="#FBBC05"
                    />
                    <path
                      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                      fill="#EA4335"
                    />
                  </svg>
                  <span>{isLoggingIn ? '正在连接 Google 账号...' : '使用 Google 账号登录并授权'}</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Logged-in User Profile Bar */}
              <div className="bg-[#1e1f20] p-3.5 rounded-xl border border-[#333538] flex items-center justify-between">
                <div className="flex items-center space-x-3">
                  {user.photoURL ? (
                    <img
                      src={user.photoURL}
                      alt={user.displayName || 'Google User'}
                      referrerPolicy="no-referrer"
                      className="w-9 h-9 rounded-full border border-blue-500/30"
                    />
                  ) : (
                    <div className="w-9 h-9 rounded-full bg-blue-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 font-bold text-xs">
                      {user.email?.charAt(0).toUpperCase() || 'G'}
                    </div>
                  )}
                  <div>
                    <div className="text-xs font-bold text-white flex items-center space-x-1.5">
                      <span>{user.displayName || 'Google Drive 用户'}</span>
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                    </div>
                    <div className="text-[11px] text-zinc-400 font-mono">{user.email}</div>
                  </div>
                </div>

                <button
                  id="btn-google-drive-sign-out"
                  onClick={handleLogout}
                  disabled={isExporting}
                  className="px-2.5 py-1 text-xs text-zinc-400 hover:text-red-400 hover:bg-[#282a2c] rounded-md transition-colors flex items-center space-x-1 cursor-pointer disabled:opacity-50"
                  title="退出 Google 账号"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  <span>退出</span>
                </button>
              </div>

              {/* Confirmation Step (Mandatory for mutating Drive files) */}
              {showConfirmStep && !isExporting && (
                <div className="bg-amber-950/30 border border-amber-800/60 p-4 rounded-xl space-y-3">
                  <div className="flex items-start space-x-2.5">
                    <AlertCircle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                    <div>
                      <h4 className="text-xs font-bold text-amber-200">
                        确认将全量前端代码保存至 Google Drive？
                      </h4>
                      <p className="text-[11px] text-amber-300/80 mt-1 leading-relaxed">
                        系统将在您的 Google Drive 中创建或更新目录{' '}
                        <span className="font-mono font-bold text-white">~/code/frontend/</span>，并上传/同步共计{' '}
                        <span className="font-bold text-white">{fileCount}</span> 个源文件（含组件、样式、工具类、配置文件）。
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center justify-end space-x-2 pt-2 border-t border-amber-800/40">
                    <button
                      id="btn-cancel-save-confirm"
                      onClick={() => setShowConfirmStep(false)}
                      className="px-3 py-1.5 bg-[#1e1f20] hover:bg-[#282a2c] text-zinc-300 text-xs rounded-lg transition-colors cursor-pointer"
                    >
                      取消
                    </button>
                    <button
                      id="btn-execute-save-confirm"
                      onClick={startExport}
                      className="px-4 py-1.5 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-lg transition-colors flex items-center space-x-1.5 cursor-pointer shadow-sm"
                    >
                      <HardDrive className="w-3.5 h-3.5 text-black" />
                      <span>确认保存 ({fileCount} 个文件)</span>
                    </button>
                  </div>
                </div>
              )}

              {/* Progress and Realtime Upload Status */}
              {progress && (
                <div className="bg-[#131314] p-4 rounded-xl border border-[#333538] space-y-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-zinc-200 flex items-center space-x-2">
                      {progress.status === 'uploading' && (
                        <RefreshCw className="w-3.5 h-3.5 text-blue-400 animate-spin" />
                      )}
                      {progress.status === 'completed' && (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      )}
                      {progress.status === 'error' && (
                        <AlertCircle className="w-4 h-4 text-red-400" />
                      )}
                      <span>
                        {progress.status === 'uploading'
                          ? '正在上传同步代码到 Google Drive...'
                          : progress.status === 'completed'
                          ? '同步成功！'
                          : progress.status === 'error'
                          ? '同步失败'
                          : '准备同步...'}
                      </span>
                    </span>

                    <span className="font-mono text-zinc-400 text-[11px]">
                      {progress.current} / {progress.total} (
                      {Math.round((progress.current / (progress.total || 1)) * 100)}%)
                    </span>
                  </div>

                  {/* Progress Bar */}
                  <div className="w-full h-2 bg-[#1e1f20] rounded-full overflow-hidden border border-[#333538]">
                    <div
                      className={`h-full transition-all duration-300 ${
                        progress.status === 'completed'
                          ? 'bg-emerald-500'
                          : progress.status === 'error'
                          ? 'bg-red-500'
                          : 'bg-blue-500'
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          Math.round((progress.current / (progress.total || 1)) * 100)
                        )}%`,
                      }}
                    />
                  </div>

                  <div className="text-[11px] text-zinc-400 font-mono truncate">
                    {progress.message}
                  </div>

                  {/* Direct Link to Google Drive folder when completed */}
                  {progress.status === 'completed' && progress.driveFolderUrl && (
                    <div className="pt-2">
                      <a
                        href={progress.driveFolderUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center space-x-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-all shadow-md cursor-pointer"
                      >
                        <HardDrive className="w-4 h-4 text-white" />
                        <span>在 Google Drive 中打开 ~/code/frontend 文件夹</span>
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                  )}

                  {/* Uploaded Files Log Accordion */}
                  {progress.uploadedFiles.length > 0 && (
                    <div className="mt-3 max-h-40 overflow-y-auto space-y-1 pr-1 border-t border-[#333538] pt-2">
                      <div className="text-[10px] text-zinc-500 font-semibold mb-1">
                        已上传文件列表 ({progress.uploadedFiles.length})：
                      </div>
                      {progress.uploadedFiles.map((file, idx) => (
                        <div
                          key={idx}
                          className="flex items-center justify-between text-[10px] font-mono py-1 px-2 rounded bg-[#1e1f20] text-zinc-300"
                        >
                          <span className="flex items-center space-x-1.5 truncate">
                            <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                            <span className="truncate">{file.path}</span>
                          </span>
                          {file.url && (
                            <a
                              href={file.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-blue-400 hover:underline shrink-0 ml-2"
                            >
                              查看
                            </a>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Codebase File Preview Box */}
              {!progress && !showConfirmStep && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs text-zinc-400">
                    <span className="font-semibold text-zinc-200">
                      待同步源码文件清单 ({fileCount})
                    </span>
                    <span className="text-[10px] font-mono">React 19 + TypeScript + Vite</span>
                  </div>

                  <div className="bg-[#131314] rounded-xl border border-[#333538] max-h-48 overflow-y-auto p-2 space-y-1 font-mono text-[11px]">
                    {Object.keys(allFiles).map((path) => (
                      <div
                        key={path}
                        className="flex items-center space-x-2 py-1 px-2 rounded hover:bg-[#1e1f20] text-zinc-300 transition-colors"
                      >
                        <FileCode className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                        <span className="truncate">{path}</span>
                        <span className="ml-auto text-[10px] text-zinc-500 shrink-0">
                          {allFiles[path].length} B
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 border-t border-[#333538] bg-[#1e1f20] flex items-center justify-between">
          <div className="text-[11px] text-zinc-400">
            {user ? (
              <span className="flex items-center space-x-1.5 text-emerald-400">
                <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                <span>已连接 Google Drive</span>
              </span>
            ) : (
              <span>需要 Google 账号授权</span>
            )}
          </div>

          <div className="flex items-center space-x-2">
            <button
              id="btn-footer-close-drive"
              onClick={onClose}
              disabled={isExporting}
              className="px-4 py-2 bg-[#131314] hover:bg-[#282a2c] text-zinc-300 text-xs rounded-lg transition-colors cursor-pointer disabled:opacity-50"
            >
              关闭
            </button>

            {user && !showConfirmStep && (
              <button
                id="btn-open-save-confirm-step"
                onClick={() => setShowConfirmStep(true)}
                disabled={isExporting}
                className="px-4 py-2 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-lg transition-all flex items-center space-x-1.5 cursor-pointer shadow-sm disabled:opacity-50"
              >
                <HardDrive className="w-3.5 h-3.5 text-black" />
                <span>
                  {progress?.status === 'completed'
                    ? '重新同步至 Google Drive'
                    : '保存代码至 Google Drive'}
                </span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

