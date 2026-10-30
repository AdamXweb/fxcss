import type { NextConfig } from 'next';
const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Documentation metadata comes from local build data. Resolve it before
  // sending HTML so every crawler receives titles and canonicals in <head>.
  htmlLimitedBots: /.*/,
};
export default nextConfig;
