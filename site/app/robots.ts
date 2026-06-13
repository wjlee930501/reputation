import { MetadataRoute } from 'next'
import { platformSiteUrl } from '@/lib/site-url'

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
    sitemap: `${platformSiteUrl()}/sitemap.xml`,
  }
}
