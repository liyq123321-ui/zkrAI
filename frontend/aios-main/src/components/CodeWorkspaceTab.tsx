import React, { useState } from 'react';
import {
  FileCode,
  Folder,
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
  Play,
  Save,
  Sparkles,
  GitBranch,
  Terminal,
  HardDrive,
} from 'lucide-react';

interface CodeFile {
  name: string;
  path: string;
  language: string;
  authorAgent: string;
  content: string;
}

interface CodeWorkspaceTabProps {
  onOpenGoogleDrive?: () => void;
}

export const CodeWorkspaceTab: React.FC<CodeWorkspaceTabProps> = ({
  onOpenGoogleDrive,
}) => {
  const [copied, setCopied] = useState(false);

  const files: CodeFile[] = [
    {
      name: 'auth.ts',
      path: 'src/middleware/auth.ts',
      language: 'typescript',
      authorAgent: 'Backend Core Agent (WI-102)',
      content: `/**
 * @license Apache-2.0
 * JWT Authentication & Blacklist Verification Middleware
 * Assigned Agent: Backend Core Agent (WI-102)
 */

import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import { isTokenRevoked } from '../services/tokenRevocation';

export interface AuthenticatedUser {
  id: string;
  email: string;
  role: 'admin' | 'engineer' | 'viewer';
  jti: string;
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthenticatedUser;
    }
  }
}

export const authMiddleware = async (
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> => {
  const authHeader = req.headers.authorization;
  if (!authHeader?.startsWith('Bearer ')) {
    res.status(401).json({
      error: 'UNAUTHORIZED',
      message: 'Authorization header with Bearer token is required',
    });
    return;
  }

  const token = authHeader.split(' ')[1];

  try {
    const decoded = jwt.verify(
      token,
      process.env.JWT_SECRET || 'dev-secret-key'
    ) as AuthenticatedUser;

    // Check token revocation blacklist (Redis)
    const revoked = await isTokenRevoked(decoded.jti);
    if (revoked) {
      res.status(401).json({
        error: 'TOKEN_REVOKED',
        message: 'This session has been logged out or revoked',
      });
      return;
    }

    req.user = decoded;
    next();
  } catch (err) {
    res.status(401).json({
      error: 'INVALID_TOKEN',
      message: 'JWT token validation failed',
    });
  }
};
`,
    },
    {
      name: 'LoginModal.tsx',
      path: 'src/components/auth/LoginModal.tsx',
      language: 'tsx',
      authorAgent: 'Frontend UI Agent (WI-103)',
      content: `import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Lock, ShieldCheck, Key, ArrowRight } from 'lucide-react';

export const LoginModal: React.FC = () => {
  const [step, setStep] = useState<'credentials' | 'totp'>('credentials');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState(['', '', '', '', '', '']);

  const handleInitialSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (email && password) {
      setStep('totp');
    }
  };

  return (
    <div className="w-full max-w-md bg-[#1e1f20] border border-[#333538] rounded-xl p-6 shadow-2xl text-[#e3e3e3]">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-xl bg-white text-black flex items-center justify-center font-bold">
          <ShieldCheck className="w-5 h-5" />
        </div>
        <div>
          <h2 className="text-base font-bold text-white">企业级安全鉴权中枢</h2>
          <p className="text-xs text-zinc-400">2FA / TOTP 动态双因子校验就绪</p>
        </div>
      </div>

      <form onSubmit={handleInitialSubmit} className="space-y-4">
        <div>
          <label className="block text-xs font-semibold text-zinc-300 mb-1">
            企业工作邮箱
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="lead@company.ai"
            className="w-full px-3 py-2 bg-[#131314] border border-[#333538] rounded-lg text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-zinc-300 mb-1">
            访问凭证密钥
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••••"
            className="w-full px-3 py-2 bg-[#131314] border border-[#333538] rounded-lg text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
          />
        </div>

        <button
          type="submit"
          className="w-full py-2 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-lg transition-colors flex items-center justify-center gap-2 cursor-pointer"
        >
          <span>验证身份并进入下一步</span>
          <ArrowRight className="w-4 h-4" />
        </button>
      </form>
    </div>
  );
};
`,
    },
    {
      name: 'security.spec.ts',
      path: 'test/security.spec.ts',
      language: 'typescript',
      authorAgent: 'QA & Security Agent (WI-104)',
      content: `import { describe, it, expect } from 'vitest';
import request from 'supertest';
import { app } from '../src/server';

describe('Security Audit Suite - JWT & Brute Force Guard', () => {
  it('should reject requests without authorization token', async () => {
    const res = await request(app).get('/api/protected/resource');
    expect(res.status).toBe(401);
    expect(res.body.error).toBe('UNAUTHORIZED');
  });

  it('should reject tampered or malformed signature tokens', async () => {
    const malformedToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fakePayload.badSig';
    const res = await request(app)
      .get('/api/protected/resource')
      .set('Authorization', \`Bearer \${malformedToken}\`);
    expect(res.status).toBe(401);
    expect(res.body.error).toBe('INVALID_TOKEN');
  });
});
`,
    },
  ];

  const [activeFileIndex, setActiveFileIndex] = useState(0);
  const activeFile = files[activeFileIndex];

  const handleCopy = () => {
    navigator.clipboard.writeText(activeFile.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      id="code-workspace-view"
      className="flex-1 flex h-full bg-[#131314] overflow-hidden text-[#e3e3e3]"
    >
      {/* File Tree Explorer Sidebar */}
      <div className="w-60 bg-[#1e1f20] border-r border-[#333538] flex flex-col shrink-0 select-none text-xs">
        <div className="p-3 border-b border-[#333538] flex items-center justify-between text-zinc-400 font-mono">
          <span className="font-semibold text-zinc-200">文件资源树</span>
          <span className="text-[10px] px-1.5 py-0.2 bg-[#131314] rounded border border-[#333538]">
            {files.length} FILES
          </span>
        </div>

        <div className="p-2 space-y-1 overflow-y-auto">
          <div className="flex items-center gap-1 text-zinc-400 px-2 py-1">
            <Folder className="w-3.5 h-3.5 text-zinc-400" />
            <span className="font-semibold">workspace-root</span>
          </div>

          {files.map((file, idx) => {
            const isSelected = idx === activeFileIndex;
            return (
              <button
                key={file.path}
                onClick={() => setActiveFileIndex(idx)}
                className={`w-full flex items-center gap-2 px-4 py-1.5 rounded-md transition-colors text-left cursor-pointer ${
                  isSelected
                    ? 'bg-[#282a2c] text-white font-semibold'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-[#282a2c]/50'
                }`}
              >
                <FileCode className="w-3.5 h-3.5 text-zinc-400" />
                <span className="truncate">{file.name}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Code Editor Pane */}
      <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#131314]">
        {/* Editor File Tab Bar */}
        <div className="h-9 bg-[#1e1f20] border-b border-[#333538] flex items-center justify-between px-4">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-mono text-zinc-300 font-semibold">
              {activeFile.path}
            </span>
            <span className="text-[10px] px-2 py-0.2 rounded bg-[#131314] text-zinc-400 border border-[#333538] font-mono">
              {activeFile.authorAgent}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {onOpenGoogleDrive && (
              <button
                id="btn-workspace-export-drive"
                onClick={onOpenGoogleDrive}
                className="px-2.5 py-1 rounded bg-blue-600/10 hover:bg-blue-600/20 text-blue-400 border border-blue-500/30 text-xs flex items-center gap-1.5 transition-colors cursor-pointer font-medium"
                title="将全量源码同步至 Google Drive (~/code/frontend)"
              >
                <HardDrive className="w-3.5 h-3.5 text-blue-400" />
                <span>保存代码至 Google Drive</span>
              </button>
            )}

            <button
              onClick={handleCopy}
              className="px-2.5 py-1 rounded bg-[#131314] hover:bg-[#282a2c] text-zinc-300 border border-[#333538] text-xs flex items-center gap-1 transition-colors cursor-pointer"
            >
              {copied ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span>已复制</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5 text-zinc-400" />
                  <span>复制代码</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Code Content Block */}
        <div className="flex-1 overflow-y-auto p-4 bg-[#0e0e10]">
          <pre className="font-mono text-xs text-zinc-200 leading-relaxed overflow-x-auto whitespace-pre">
            {activeFile.content}
          </pre>
        </div>
      </div>
    </div>
  );
};

