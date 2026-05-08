interface Props {
  data: Record<string, unknown> | Array<Record<string, unknown>>
}

export function JsonLd({ data }: Props) {
  return (
    <script
      type="application/ld+json"
      // 직접 직렬화한 결과만 주입한다. `<` 문자를 escape하여 HTML parser 혼란 방지.
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, '\\u003c') }}
    />
  )
}
