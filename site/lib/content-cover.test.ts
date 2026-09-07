import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as React from 'react'
import * as jsxRuntime from 'react/jsx-runtime'
import { renderToStaticMarkup } from 'react-dom/server'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(
  new URL('../app/[slug]/_components/ContentCover.tsx', import.meta.url),
  'utf8',
)

test('representative content images are present in server-rendered HTML', () => {
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  const compiledModule = { exports: {} as Record<string, unknown> }
  const image = ({ src, alt }: { src: string; alt: string }) =>
    React.createElement('img', { src, alt })
  const customRequire = (id: string): unknown => {
    if (id === 'react') return { ...requireReact(), default: requireReact() }
    if (id === 'react/jsx-runtime') return jsxRuntime
    if (id === 'next/image') return { __esModule: true, default: image }
    if (id === '@/components/brand') return { ContentMotif: () => React.createElement('span') }
    if (id === '@/lib/image-policy') return { isOffAllowlistExternalUrl: () => false }
    throw new Error(`Unexpected module: ${id}`)
  }
  Function('require', 'exports', 'module', compiled)(
    customRequire,
    compiledModule.exports,
    compiledModule,
  )
  const ContentCover = compiledModule.exports.ContentCover as React.ComponentType<
    Record<string, unknown>
  >
  const html = renderToStaticMarkup(
    React.createElement(ContentCover, {
      type: 'FAQ',
      src: 'https://images.example/cover.png',
      alt: 'FAQ: 회복 기간 안내',
      variant: 'featured',
    }),
  )
  assert.match(html, /<img[^>]+src="https:\/\/images\.example\/cover\.png"/)
  assert.match(html, /alt="FAQ: 회복 기간 안내"/)
})

function requireReact(): typeof import('react') {
  // CommonJS로 transpile한 컴포넌트의 `require('react')`에 현재 React 인스턴스를 제공한다.
  return React
}
