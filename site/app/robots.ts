import { MetadataRoute } from 'next'

const DISALLOWED_PATHS = ['/api/', '/_next/', '/.well-known/']

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: DISALLOWED_PATHS,
      },
      {
        userAgent: [
          'GPTBot',
          'OAI-SearchBot',
          'ChatGPT-User',
          'PerplexityBot',
          'ClaudeBot',
          'anthropic-ai',
          'Google-Extended',
          'Bingbot',
          'Googlebot',
        ],
        allow: '/',
        disallow: DISALLOWED_PATHS,
      },
    ],
    sitemap: `${process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'}/sitemap.xml`,
  }
}
