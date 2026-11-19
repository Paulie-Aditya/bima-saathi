"use client";

import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Upload({ params }: { params: Promise<{ claimId: string }> }) {
  const { claimId } = use(params);
  const [claim, setClaim] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api(`/api/claims/${claimId}`).then(setClaim).catch((e) => setErr(e.message));
  }, [claimId]);

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    fd.append("file", f);
    try {
      setResult(await api(`/api/claims/${claimId}/documents`, fd));
      setClaim(await api(`/api/claims/${claimId}`));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="shell">
      <div className="panel upload">
        <div className="panel-h">
          <h2>Upload documents · {claimId}</h2>
        </div>
        <div className="panel-b">
          {claim ? (
            <p>
              {claim.patient} · {claim.procedure}
              <br />
              {claim.pending_labels.length ? (
                <>
                  Still needed: <b>{claim.pending_labels.join(", ")}</b>
                </>
              ) : (
                <b>Nothing pending. Thank you!</b>
              )}
            </p>
          ) : null}
          <label className="drop">
            {busy ? "Reading your document…" : "Tap to take a photo or choose a file"}
            <input
              type="file"
              accept="image/*,application/pdf"
              capture="environment"
              style={{ display: "none" }}
              onChange={(e) => onFile(e.target.files?.[0])}
            />
          </label>
          {result ? (
            <p style={{ marginTop: 16 }}>
              ✅ {result.summary}
              {result.still_missing?.length ? <> · Still missing: {result.still_missing.join(", ")}</> : null}
            </p>
          ) : null}
          {err ? <div className="banner" style={{ margin: "12px 0 0" }}>{err}</div> : null}
        </div>
      </div>
    </main>
  );
}
