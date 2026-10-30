import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: the site is plain files on S3 behind CloudFront (see infra/). Nothing here needs a
  // Node server today; revisit if a route ever needs request-time rendering.
  output: "export",
  // /incidents/INC-1001/index.html rather than INC-1001.html, so CloudFront needs only a rewrite to index.html.
  trailingSlash: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
