import type { Option, Variant } from "../types";
import { valueDisabled, type Selections } from "../lib/resolveVariant";

interface Props {
  options: Option[];
  variants: Variant[];
  selections: Selections;
  onSelect: (axis: string, value: string) => void;
}

// one button group per axis; values no variant supports (given the other
// chosen axes) disable, out-of-stock resolved values strike through on the
// availability line rather than here
export function VariantPicker({ options, variants, selections, onSelect }: Props) {
  if (options.length === 0) return null;

  return (
    <div className="space-y-5">
      {options.map((opt) => (
        <fieldset key={opt.name}>
          <legend className="eyebrow mb-2">
            {opt.name}
            {selections[opt.name] && (
              <span className="ml-2 normal-case tracking-normal text-ink">{selections[opt.name]}</span>
            )}
          </legend>
          <div className="flex flex-wrap gap-2">
            {opt.values.map((value) => {
              const selected = selections[opt.name] === value;
              const disabled = !selected && valueDisabled(variants, selections, opt.name, value, options);
              return (
                <button
                  key={value}
                  onClick={() => onSelect(opt.name, value)}
                  disabled={disabled}
                  aria-pressed={selected}
                  className={`border px-3 py-1.5 text-sm transition-colors ${
                    selected
                      ? "border-ink bg-ink text-white"
                      : disabled
                        ? "cursor-not-allowed border-line text-faint line-through"
                        : "border-line hover:border-ink"
                  }`}
                >
                  {value}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
