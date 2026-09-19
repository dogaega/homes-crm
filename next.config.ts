import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  typescript: {
    // Temporarily ignore TypeScript errors during build
    ignoreBuildErrors: true,
  },
  experimental: {
    optimizePackageImports: ['lucide-react', 'react-hook-form'],
  },
  images: {
    // The Workers runtime has no sharp binary, so Next's built-in image
    // optimizer can't run there — serve remote images as-is instead.
    unoptimized: true,
    remotePatterns: [
      { protocol: 'https', hostname: '*.workers.dev' },
    ],
  },
  async rewrites() {
    // Same-origin proxy to the Cloudflare Worker API so the httpOnly
    // session cookie works without CORS. WORKER_URL points at
    // `wrangler dev` locally, or the deployed Worker in production.
    const workerUrl = process.env.WORKER_URL || 'http://localhost:8787'
    return [
      { source: '/api/backend/:path*', destination: `${workerUrl}/:path*` },
    ]
  },
  turbopack: {
    rules: {
      '*.svg': {
        loaders: ['@svgr/webpack'],
        as: '*.js',
      },
    },
  },
  webpack: (config, { dev, isServer }) => {
    if (dev) {
      config.watchOptions = {
        poll: 1000,
        aggregateTimeout: 300,
        ignored: /node_modules/,
      };
    }
    
    return config;
  },
  compress: true,
};

// Sentry configuration
const sentryWebpackPluginOptions = {
  // Additional config options for the Sentry Webpack plugin
  silent: true, // Suppresses all logs
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  
  // Upload source maps to Sentry
  widenClientFileUpload: true,
  
  // Transpile SDK to be compatible with IE11
  transpileClientSDK: true,
  
  // Hide source maps from generated client bundles
  hideSourceMaps: true,
  
  // Automatically tree-shake Sentry logger statements
  disableLogger: true,
  
  // Enable automatic instrumentation of Vercel Cron Monitors
  automaticVercelMonitors: true,
};

export default withSentryConfig(nextConfig, sentryWebpackPluginOptions);

import('@opennextjs/cloudflare').then(m => m.initOpenNextCloudflareForDev());
