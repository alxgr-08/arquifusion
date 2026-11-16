import { Component, type ReactNode } from "react";

/** A failed import after a new web release asks the stale page to reload; any other render error stays on this page. */
const isStaleBundle = (error: Error) =>
  /dynamically imported module|Importing a module script failed|error loading dynamically imported/i.test(error.message);

export class ErrorBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = isStaleBundle(error);
    return (
      <div className="banner-error" role="alert">
        <span>
          {stale
            ? "Hay una versión nueva de la web. Recarga la página para continuar."
            : `Esta sección falló al mostrarse: ${error.message}`}
        </span>
        {stale
          ? <button onClick={() => window.location.reload()}>Recargar</button>
          : <button onClick={() => this.setState({ error: undefined })}>Reintentar</button>}
      </div>
    );
  }
}
