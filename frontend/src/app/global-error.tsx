"use client";

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return (
    <html lang="ru">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "3rem", lineHeight: 1.5 }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Что-то пошло не так</h1>
        <p style={{ color: "#666" }}>Не удалось показать эту страницу.</p>
        {error.digest && (
          <p style={{ color: "#666", fontFamily: "monospace", fontSize: "0.75rem" }}>
            Код обращения: {error.digest}
          </p>
        )}
        <button
          onClick={() => unstable_retry()}
          style={{
            marginTop: "1rem",
            padding: "0.5rem 1rem",
            borderRadius: "0.5rem",
            border: "1px solid #ccc",
            cursor: "pointer",
          }}
        >
          Повторить
        </button>
      </body>
    </html>
  );
}
