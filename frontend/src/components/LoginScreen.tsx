import { useEffect, useRef, useState } from "react";
import { renderGoogleButton, type Session } from "../lib/auth";

export function LoginScreen({ onSession }: { onSession: (s: Session) => void }) {
  const button = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (button.current) renderGoogleButton(button.current, onSession).catch((e: Error) => setError(e.message));
  }, [onSession]);

  return (
    <main className="login">
      <div className="card">
        <h1>ArquiFusion Fleet Intelligence</h1>
        <p className="muted">Inicia sesión con tu cuenta de Google para ver la operación.</p>
        <div ref={button} />
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}
