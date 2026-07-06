// Add Repository wizard: Source -> Options -> Review -> Index (live progress).
// Every option maps 1:1 to a CLI flag (`entrygraph index <source> --ref ...`),
// and the review step echoes the equivalent command — the UI teaches the CLI.

import {
  Button,
  Checkbox,
  Details,
  Flash,
  FormControl,
  Heading,
  Label,
  ProgressBar,
  Radio,
  RadioGroup,
  SegmentedControl,
  TextInput,
  useDetails,
} from "@primer/react";
import { CheckIcon, GitBranchIcon, RepoIcon } from "@primer/octicons-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useReducer, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, keys } from "../../api/queries";
import { useAuth } from "../../auth/AuthProvider";
import { useJob } from "../jobs/useJob";

import { cliEcho, validateSource, type WizardState } from "./wizardLogic";

type Action =
  | { type: "set"; patch: Partial<WizardState> }
  | { type: "next" }
  | { type: "back" };

function reducer(state: WizardState, action: Action): WizardState {
  switch (action.type) {
    case "set":
      return { ...state, ...action.patch };
    case "next":
      return { ...state, step: Math.min(2, state.step + 1) as WizardState["step"] };
    case "back":
      return { ...state, step: Math.max(0, state.step - 1) as WizardState["step"] };
  }
}

const STEPS = ["Source", "Options", "Review & index"];

export function AddRepoWizard() {
  const navigate = useNavigate();
  const { me } = useAuth();
  const [state, dispatch] = useReducer(reducer, {
    step: 0,
    sourceKind: "url",
    source: "",
    ref: "",
    fullClone: false,
    includeTests: false,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const advanced = useDetails({ closeOnOutsideClick: false });

  const register = useMutation({
    mutationFn: () =>
      api.registerRepo({
        source: state.source.trim(),
        ref: state.ref.trim() || undefined,
        depth: state.fullClone ? 0 : 1,
        include_tests: state.includeTests,
      }),
    onSuccess: (r) => setJobId(r.job_id),
  });

  const sourceError = validateSource(state.sourceKind, state.source);
  const isViewer = me != null && !me.auth_disabled && me.user.role !== "admin";

  if (isViewer) {
    return (
      <Flash variant="warning">
        Adding repositories requires the admin role — ask an administrator.
      </Flash>
    );
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 4 }}>
        Add a repository
      </Heading>
      <p className="muted" style={{ marginTop: 0 }}>
        entrygraph clones (or reads) the repository, parses every file, and builds a
        queryable graph of symbols, calls, and entrypoints. Nothing in the repo is executed.
      </p>

      {/* step rail */}
      <div className="row" style={{ margin: "16px 0" }}>
        {STEPS.map((label, i) => (
          <span key={label} className="row" style={{ gap: 6 }}>
            {i > 0 && <span className="muted">—</span>}
            <Label
              size="large"
              variant={i < state.step ? "success" : i === state.step ? "accent" : "secondary"}
            >
              {i < state.step ? <CheckIcon size={12} /> : `${i + 1}.`} {label}
            </Label>
          </span>
        ))}
      </div>

      {state.step === 0 && (
        <div className="card" style={{ padding: 20 }}>
          <SegmentedControl
            aria-label="Source type"
            onChange={(i) =>
              dispatch({ type: "set", patch: { sourceKind: i === 0 ? "url" : "path", source: "" } })
            }
          >
            <SegmentedControl.Button selected={state.sourceKind === "url"} leadingIcon={GitBranchIcon}>
              Git URL
            </SegmentedControl.Button>
            <SegmentedControl.Button selected={state.sourceKind === "path"} leadingIcon={RepoIcon}>
              Local path
            </SegmentedControl.Button>
          </SegmentedControl>

          <FormControl required style={{ marginTop: 16 }}>
            <FormControl.Label>
              {state.sourceKind === "url" ? "Repository URL" : "Absolute path on the server"}
            </FormControl.Label>
            <FormControl.Caption>
              {state.sourceKind === "url" ? (
                <>
                  https or ssh. Private repos work when the server's ambient git auth (SSH
                  agent, credential helper) can reach them — entrygraph never stores secrets.
                </>
              ) : (
                <>A directory the server process can read, e.g. a checkout on this machine.</>
              )}
            </FormControl.Caption>
            <TextInput
              className="mono"
              style={{ width: "100%", marginTop: 4 }}
              placeholder={
                state.sourceKind === "url" ? "https://github.com/org/repo" : "/path/to/checkout"
              }
              value={state.source}
              onChange={(e) => dispatch({ type: "set", patch: { source: e.target.value } })}
            />
            {state.source && sourceError && (
              <FormControl.Validation variant="error">{sourceError}</FormControl.Validation>
            )}
          </FormControl>

          <div className="row" style={{ marginTop: 20 }}>
            <span className="spacer" />
            <Button variant="primary" disabled={Boolean(sourceError)} onClick={() => dispatch({ type: "next" })}>
              Next: options
            </Button>
          </div>
        </div>
      )}

      {state.step === 1 && (
        <div className="card" style={{ padding: 20 }}>
          {state.sourceKind === "url" && (
            <FormControl>
              <FormControl.Label>Branch, tag, or commit</FormControl.Label>
              <FormControl.Caption>
                Leave empty to index the remote's default branch (usually main).
              </FormControl.Caption>
              <TextInput
                className="mono"
                placeholder="main"
                value={state.ref}
                onChange={(e) => dispatch({ type: "set", patch: { ref: e.target.value } })}
              />
            </FormControl>
          )}

          <Details {...advanced.getDetailsProps()} style={{ marginTop: 16 }}>
            <Details.Summary>Advanced options</Details.Summary>
            <div style={{ paddingTop: 12, display: "flex", flexDirection: "column", gap: 12 }}>
              {state.sourceKind === "url" && (
                <RadioGroup name="clone-depth">
                  <RadioGroup.Label>Clone depth</RadioGroup.Label>
                  <FormControl>
                    <Radio
                      value="shallow"
                      checked={!state.fullClone}
                      onChange={() => dispatch({ type: "set", patch: { fullClone: false } })}
                    />
                    <FormControl.Label>Shallow (recommended)</FormControl.Label>
                    <FormControl.Caption>
                      Only the latest commit — faster, and indexing only needs the tree.
                    </FormControl.Caption>
                  </FormControl>
                  <FormControl>
                    <Radio
                      value="full"
                      checked={state.fullClone}
                      onChange={() => dispatch({ type: "set", patch: { fullClone: true } })}
                    />
                    <FormControl.Label>Full history</FormControl.Label>
                  </FormControl>
                </RadioGroup>
              )}
              <FormControl>
                <Checkbox
                  checked={state.includeTests}
                  onChange={(e) => dispatch({ type: "set", patch: { includeTests: e.target.checked } })}
                />
                <FormControl.Label>Index test files</FormControl.Label>
                <FormControl.Caption>
                  Tests are recorded but not parsed by default; include them to query test code too.
                </FormControl.Caption>
              </FormControl>
            </div>
          </Details>

          <div className="row" style={{ marginTop: 20 }}>
            <Button onClick={() => dispatch({ type: "back" })}>Back</Button>
            <span className="spacer" />
            <Button variant="primary" onClick={() => dispatch({ type: "next" })}>
              Next: review
            </Button>
          </div>
        </div>
      )}

      {state.step === 2 && (
        <div className="card" style={{ padding: 20 }}>
          {!jobId ? (
            <>
              <div className="row">
                <span style={{ width: 90 }} className="muted fs0">
                  SOURCE
                </span>
                <span className="mono clip">{state.source}</span>
              </div>
              {state.ref && (
                <div className="row" style={{ marginTop: 4 }}>
                  <span style={{ width: 90 }} className="muted fs0">
                    REF
                  </span>
                  <span className="mono">{state.ref}</span>
                </div>
              )}
              <div className="muted fs0" style={{ marginTop: 12 }}>
                Equivalent CLI command:
              </div>
              <pre className="mono fs0 card" style={{ padding: 10, marginTop: 4 }}>
                {cliEcho(state)}
              </pre>
              {register.error && <Flash variant="danger">{String(register.error)}</Flash>}
              <div className="row" style={{ marginTop: 16 }}>
                <Button onClick={() => dispatch({ type: "back" })}>Back</Button>
                <span className="spacer" />
                <Button variant="primary" loading={register.isPending} onClick={() => register.mutate()}>
                  Start indexing
                </Button>
              </div>
            </>
          ) : (
            <IndexingProgress jobId={jobId} onExplore={(repoId) => navigate(`/repos/${repoId}`)} />
          )}
        </div>
      )}
    </div>
  );
}

function IndexingProgress({
  jobId,
  onExplore,
}: {
  jobId: string;
  onExplore: (repoId: number) => void;
}) {
  const job = useJob(jobId);
  const detect = useQuery({
    queryKey: keys.detect(job?.repo_id ?? -1),
    queryFn: () => api.detect(job!.repo_id!),
    enabled: job?.status === "succeeded" && job.repo_id != null,
  });

  if (!job) return <ProgressBar progress={2} aria-label="starting" />;

  if (job.status === "failed") {
    return (
      <>
        <Flash variant="danger">
          Indexing failed. {job.error?.split("\n")[0]}
        </Flash>
        <pre className="mono fs0 muted" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
          {job.error}
        </pre>
      </>
    );
  }
  if (job.status === "cancelled") {
    return <Flash variant="warning">Indexing was cancelled.</Flash>;
  }
  if (job.status !== "succeeded") {
    return (
      <>
        <div className="row" style={{ marginBottom: 8 }}>
          <span style={{ fontWeight: 600 }}>
            {job.phase === "cloning" ? "Cloning…" : "Indexing…"}
          </span>
          <span className="muted fs0">{job.message}</span>
        </div>
        <ProgressBar progress={Math.max(2, job.progress * 100)} aria-label="index progress" />
        <p className="muted fs0" style={{ marginTop: 10 }}>
          Phases: clone → walk the tree → parse & extract symbols → resolve references →
          write the graph. You can leave this page — the job keeps running (see Jobs).
        </p>
      </>
    );
  }
  return (
    <>
      <Flash variant="success">Indexed successfully.</Flash>
      {job.stats && (
        <div className="stats" style={{ marginTop: 12 }}>
          {(
            [
              ["Symbols", job.stats.symbols],
              ["Call edges", job.stats.edges],
              ["Entrypoints", job.stats.entrypoints],
              ["Files", job.stats.files_indexed],
            ] as Array<[string, number]>
          ).map(([l, n]) => (
            <div key={l} className="card stat">
              <div className="n">{n.toLocaleString()}</div>
              <div className="l">{l}</div>
            </div>
          ))}
        </div>
      )}
      {detect.data && (
        <div className="row wrap" style={{ marginTop: 12 }}>
          <span className="muted fs0">Detected:</span>
          {detect.data.languages.map((l) => (
            <Label key={l.name}>{l.name}</Label>
          ))}
          {detect.data.frameworks.map((f) => (
            <Label key={f.name} variant="accent">
              {f.name}
            </Label>
          ))}
        </div>
      )}
      <div className="row" style={{ marginTop: 16 }}>
        {job.repo_id != null && (
          <>
            <Button variant="primary" onClick={() => onExplore(job.repo_id!)}>
              Explore repository
            </Button>
            <Button as={Link} to={`/repos/${job.repo_id}/reachability`}>
              Run a reachability check
            </Button>
          </>
        )}
      </div>
    </>
  );
}
