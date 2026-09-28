const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // This app is one package inside a polyglot monorepo. Pinning the tracing root stops
  // Next from walking up to the user's home directory when it finds a stray lockfile
  // there, which otherwise bloats the standalone output and prints a warning per build.
  outputFileTracingRoot: path.join(__dirname),

  // Same-origin proxy for local development. The browser client normally talks to the API
  // directly via NEXT_PUBLIC_API_BASE; this rewrite is the fallback for the case where
  // only the Next port is exposed. WebSockets are *not* proxied here — Next's rewrite
  // does not upgrade connections, so NEXT_PUBLIC_API_BASE must point at the API host for
  // live progress to work.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.BACKEND_ORIGIN ?? 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
