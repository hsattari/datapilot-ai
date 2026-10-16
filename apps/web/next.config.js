/** @type {import('next').NextConfig} */
module.exports = {
  // The browser only talks to /api/*; the tenant is resolved server-side from the bearer token.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.API_URL || "http://localhost:8000"}/api/:path*` }];
  },
};
