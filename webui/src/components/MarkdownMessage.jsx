import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function MarkdownMessage({ content }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:font-semibold prose-headings:mt-4 prose-headings:mb-2 prose-p:my-1.5 prose-li:my-0.5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="overflow-x-auto my-3 rounded-lg border border-gray-200 dark:border-gray-700">
              <table className="min-w-full text-sm divide-y divide-gray-200 dark:divide-gray-700">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-gray-50 dark:bg-gray-800/60">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 dark:text-gray-300 uppercase tracking-wider whitespace-nowrap">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-2 text-gray-700 dark:text-gray-300 border-t border-gray-100 dark:border-gray-800">
              {children}
            </td>
          ),
          tr: ({ children }) => (
            <tr className="hover:bg-gray-50 dark:hover:bg-gray-800/40 transition-colors">
              {children}
            </tr>
          ),
          code: ({ inline, className, children }) => {
            if (inline) {
              return (
                <code className="px-1.5 py-0.5 rounded-md bg-gray-100 dark:bg-gray-800 text-brand-600 dark:text-brand-400 text-xs font-mono">
                  {children}
                </code>
              )
            }
            return (
              <div className="my-3 rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
                <pre className="bg-gray-50 dark:bg-gray-900 p-3 overflow-x-auto">
                  <code className={`text-xs font-mono text-gray-800 dark:text-gray-200 ${className || ''}`}>
                    {children}
                  </code>
                </pre>
              </div>
            )
          },
          pre: ({ children }) => <>{children}</>,
          ul: ({ children }) => (
            <ul className="list-disc list-inside space-y-0.5 my-2 text-gray-700 dark:text-gray-300">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal list-inside space-y-0.5 my-2 text-gray-700 dark:text-gray-300">
              {children}
            </ol>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-3 border-brand-400 dark:border-brand-500 pl-3 my-2 text-gray-600 dark:text-gray-400 italic">
              {children}
            </blockquote>
          ),
          h1: ({ children }) => (
            <h1 className="text-lg font-bold text-gray-900 dark:text-white">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-bold text-gray-900 dark:text-white">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-bold text-gray-800 dark:text-gray-100">{children}</h3>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-600 dark:text-brand-400 hover:underline"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-4 border-gray-200 dark:border-gray-700" />,
          strong: ({ children }) => (
            <strong className="font-semibold text-gray-900 dark:text-white">{children}</strong>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownMessage
