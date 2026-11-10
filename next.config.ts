import type { NextConfig } from 'next';
const nextConfig: NextConfig = {
  // Every page is built to a file, so no site code runs per request.
  output: 'export',
  poweredByHeader: false,
  // Documentation metadata comes from local build data. Resolve it before
  // sending HTML so every crawler receives titles and canonicals in <head>.
  htmlLimitedBots: /.*/,
};
export default nextConfig;
