import { Button, Text } from "@primer/react";
import { ShareAndroidIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { ErrorFlash, Loading } from "../../components/ui";
import { useRepoId } from "./RepoLayout";
import type { Symbol as Sym } from "../../api/types";

function SymbolLinkList({ title, symbols }: { title: string; symbols: Sym[] }) {
  const repoId = useRepoId();
  return (
    <div className="section">
      <Text className="muted fs0" style={{ fontWeight: 600 }}>
        {title} ({symbols.length})
      </Text>
      {symbols.length === 0 && <div className="muted fs0">none</div>}
      {symbols.slice(0, 30).map((s) => (
        <div key={s.id} className="clip">
          <Link
            className="mono fs0"
            to={`/repos/${repoId}/symbols?sel=${encodeURIComponent(s.qname)}`}
          >
            {s.qname}
          </Link>
        </div>
      ))}
      {symbols.length > 30 && <div className="muted fs0">…and {symbols.length - 30} more</div>}
    </div>
  );
}

export function SymbolDetailPane({ qname }: { qname: string }) {
  const repoId = useRepoId();
  const { data, isPending, error } = useQuery({
    queryKey: keys.symbolDetail(repoId, qname),
    queryFn: () => api.symbolDetail(repoId, qname),
  });

  return (
    <aside className="card detail">
      {isPending ? (
        <Loading />
      ) : error ? (
        <ErrorFlash message={String(error)} />
      ) : (
        <>
          <div className="mono" style={{ fontWeight: 600, wordBreak: "break-all" }}>
            {data.symbol.qname}
          </div>
          <div className="muted fs0" style={{ marginTop: 4 }}>
            {data.symbol.kind}
            {data.symbol.file && (
              <>
                {" · "}
                {data.symbol.file}
                {data.symbol.line ? `:${data.symbol.line}` : ""}
              </>
            )}
          </div>
          {data.symbol.signature && (
            <pre className="mono fs0" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
              {data.symbol.signature}
            </pre>
          )}
          <div className="section">
            <Button
              as={Link}
              to={`/repos/${repoId}/graph?focus=${encodeURIComponent(qname)}`}
              leadingVisual={ShareAndroidIcon}
              size="small"
            >
              View in call graph
            </Button>
          </div>
          <SymbolLinkList title="CALLERS" symbols={data.callers} />
          <SymbolLinkList title="CALLEES" symbols={data.callees} />
        </>
      )}
    </aside>
  );
}
