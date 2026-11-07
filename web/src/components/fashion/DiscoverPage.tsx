import { ImageUp, Search, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState, type DragEvent } from "react";

import { examplePhotoUrl } from "../../api/client";
import {
  type QueryImage,
  useImageSearch,
  useSearchExamples,
  useSections,
  useTextSearch,
} from "../../api/queries";
import type { QueryPhoto } from "../../api/types";
import { MAX_UPLOAD_MB, TOP_K, textSearchBody, uploadProblem } from "../../domains/requests";
import { isNumberOrNull, isStringOrNull, oneOf, usePageState } from "../../state/pageState";
import { Button } from "../../ui/Button";
import { Field, Select, TextInput } from "../../ui/Field";
import { Photo } from "../../ui/Photo";
import { Segmented } from "../../ui/Segmented";
import { StateMessage } from "../../ui/StateMessage";
import { Shimmer } from "../../ui/Skeleton";
import { ItemGrid, ProductCard } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Examples } from "../browse/Examples";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import styles from "./Discover.module.css";

const TEXT_EXAMPLES = ["striped summer dress", "white sneakers", "chunky knit cardigan"];

function DescriptionSearch() {
  const sections = useSections();
  const [draft, setDraft] = usePageState("discover.draft", "");
  const [query, setQuery] = usePageState("discover.query", "");
  const [section, setSection] = usePageState<string | null>(
    "discover.section",
    null,
    isStringOrNull,
  );
  const body = textSearchBody(query, section);
  const results = useTextSearch(body);
  return (
    <>
      <form
        className={browse.panel}
        aria-label="Describe your piece"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft);
        }}
      >
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Describe your piece</h2>
          <p className={browse.panelIntro}>
            Curio compares your words with every product photo, so colours, cuts and patterns all
            count.
          </p>
        </div>
        <div className={styles.textRow}>
          <Field label="Describe what you are looking for">
            {({ id }) => (
              <TextInput
                id={id}
                search
                value={draft}
                maxLength={300}
                placeholder="e.g. a chunky cream cardigan"
                autoComplete="off"
                enterKeyHint="search"
                onChange={(event) => {
                  setDraft(event.target.value);
                }}
              />
            )}
          </Field>
          <Field label="Section">
            {({ id }) => (
              <Select
                id={id}
                value={section ?? ""}
                onChange={(event) => {
                  setSection(event.target.value || null);
                }}
              >
                <option value="">All</option>
                {(sections.data ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Button type="submit" variant="primary" icon={<Search />} className={styles.submit}>
            Search photos
          </Button>
        </div>
        <Examples
          examples={TEXT_EXAMPLES}
          current={query}
          onPick={(example) => {
            setDraft(example);
            setQuery(example);
          }}
        />
      </form>
      <Results
        title="Matching pieces"
        query={results}
        enabled={body !== null}
        kind="product"
        noun="piece"
        idle="Describe a piece, or try an example, to search the catalog's photos."
        empty="No matching products. Try another description or section."
      >
        {(hits) => (
          <ItemGrid label="Matching pieces">
            {hits.map((hit) => (
              <ProductCard key={hit.product.product_id} product={hit.product} detailed />
            ))}
          </ItemGrid>
        )}
      </Results>
    </>
  );
}

function exampleImage(photo: QueryPhoto): QueryImage {
  return {
    key: `example:${String(photo.photo_id)}`,
    name: `${String(photo.photo_id)}.jpg`,
    load: async () => {
      const response = await fetch(examplePhotoUrl(photo.photo_id));
      if (!response.ok) throw new Error("The example photo is no longer available.");
      return response.blob();
    },
  };
}

function PhotoSearch() {
  const examples = useSearchExamples();
  const [exampleId, setExampleId] = usePageState<number | null>(
    "discover.example",
    null,
    isNumberOrNull,
  );
  const [upload, setUpload] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const example = examples.data?.find((e) => e.photo_id === exampleId) ?? null;
  const image = useMemo<QueryImage | null>(() => {
    if (upload) {
      return {
        key: `upload:${upload.name}:${String(upload.size)}:${String(upload.lastModified)}`,
        name: upload.name,
        load: () => Promise.resolve(upload),
      };
    }
    return example ? exampleImage(example) : null;
  }, [upload, example]);
  const preview = useMemo(() => (upload ? URL.createObjectURL(upload) : null), [upload]);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const results = useImageSearch(image, TOP_K.pieces);

  function choose(file: File | undefined) {
    if (!file) return;
    const issue = uploadProblem(file);
    setProblem(issue);
    if (issue) return;
    setUpload(file);
    setExampleId(null);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  const queryPhoto = preview ?? (example ? examplePhotoUrl(example.photo_id) : null);
  const queryLabel = upload
    ? upload.name
    : example
      ? `${example.name} · ${example.colour}`
      : undefined;

  return (
    <>
      <section className={browse.panel} aria-label="Start with a photo">
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Start with a photo</h2>
          <p className={browse.panelIntro}>
            Upload a photo of a garment, or pick an example. Curio finds the pieces that look most
            alike.
          </p>
        </div>
        <div
          className={styles.drop}
          data-dragging={dragging ? "true" : undefined}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => {
            setDragging(false);
          }}
          onDrop={onDrop}
        >
          <ImageUp size={26} aria-hidden="true" className={styles.dropIcon} />
          <div className={styles.dropText}>
            <label htmlFor={inputId} className={styles.dropLabel}>
              Upload a photo of a garment
            </label>
            <span className={styles.dropHint}>
              Drag it here, or choose a file · PNG or JPEG, up to {MAX_UPLOAD_MB} MB
            </span>
          </div>
          <Button
            onClick={() => {
              fileInput.current?.click();
            }}
          >
            Choose a photo
          </Button>
          <input
            ref={fileInput}
            id={inputId}
            type="file"
            accept="image/png,image/jpeg"
            className="visually-hidden"
            onChange={(event) => {
              choose(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
        {problem ? (
          <p className={styles.problem} role="alert">
            {problem}
          </p>
        ) : null}
        {examples.isPending ? (
          <Shimmer height="6rem" />
        ) : examples.data?.length ? (
          <div className={styles.examples}>
            <p className={styles.examplesHead}>
              <Sparkles size={15} aria-hidden="true" /> Or pick an example photo
              <span className={styles.examplesNote}>
                Garments outside this catalog; results show similar products.
              </span>
            </p>
            <ul className={styles.exampleGrid} aria-label="Example photos">
              {examples.data.map((photo) => {
                const active = !upload && photo.photo_id === exampleId;
                return (
                  <li key={photo.photo_id}>
                    <button
                      type="button"
                      className={styles.example}
                      aria-pressed={active}
                      title={`${photo.name} · ${photo.colour}`}
                      onClick={() => {
                        setUpload(null);
                        setProblem(null);
                        setExampleId(photo.photo_id);
                      }}
                    >
                      <Photo
                        sources={[examplePhotoUrl(photo.photo_id)]}
                        alt=""
                        ratio="3 / 4"
                        paper
                        placeholder=""
                      />
                      <span className={styles.exampleLabel}>{photo.product_type}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </section>
      {image && queryPhoto ? (
        <div className={styles.photoResults}>
          <AnimatePresence mode="wait">
            <motion.figure
              key={image.key}
              className={styles.query}
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            >
              <figcaption className={styles.queryTitle}>Your photo</figcaption>
              <Photo sources={[queryPhoto]} alt={queryLabel ?? "Query photo"} ratio="3 / 4" paper />
              {queryLabel ? <p className={styles.queryLabel}>{queryLabel}</p> : null}
            </motion.figure>
          </AnimatePresence>
          <Results
            title="Visually similar pieces"
            query={results}
            enabled
            kind="product"
            noun="piece"
            empty="No similar pieces were found for this photo."
          >
            {(hits) => (
              <ItemGrid label="Visually similar pieces">
                {hits.map((hit) => (
                  <ProductCard key={hit.product.product_id} product={hit.product} detailed />
                ))}
              </ItemGrid>
            )}
          </Results>
        </div>
      ) : (
        <StateMessage tone="hint">
          Upload a photo or pick an example to see visually similar pieces.
        </StateMessage>
      )}
    </>
  );
}

type Mode = "description" | "photo";

/** Discover: search the catalog by a description or by a photo. */
export function DiscoverPage() {
  const [mode, setMode] = usePageState<Mode>(
    "discover.mode",
    "description",
    oneOf<Mode>(["description", "photo"]),
  );
  return (
    <div className={browse.page}>
      <PageHeader
        title="Discover"
        documentTitle="Discover · Fashion"
        intro="Find pieces by describing them or by showing a photo. Every result is a real catalog product."
      />
      <Segmented
        label="Search with"
        options={[
          { value: "description", label: "Description", icon: <Search /> },
          { value: "photo", label: "Photo", icon: <ImageUp /> },
        ]}
        value={mode}
        onChange={setMode}
      />
      {mode === "description" ? <DescriptionSearch /> : <PhotoSearch />}
    </div>
  );
}
