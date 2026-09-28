import type { Metadata } from 'next';
import Link from 'next/link';
import '@/styles/globals.css';
import { BackendStatus } from '@/components/BackendStatus';

export const metadata: Metadata = {
  title: 'MONTA — AI Video Editor',
  description: 'Tell MONTA what you want. It edits for you.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <Link href="/" className="brand">
            🎬 MONTA
          </Link>
          <span className="spacer" />
          <BackendStatus />
        </header>
        <main className="app-main">{children}</main>
      </body>
    </html>
  );
}
