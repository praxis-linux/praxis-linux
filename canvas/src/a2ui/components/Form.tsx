import type { FormNode } from "../types";

type Props = FormNode["props"];

export default function Form({ fields, submitLabel = "Submit" }: Props) {
  return (
    <form className="a2ui-form" onSubmit={(e) => e.preventDefault()}>
      {fields.map((field) => (
        <div key={field.name} className="form-field">
          <label htmlFor={field.name}>{field.label}</label>
          {field.type === "textarea" ? (
            <textarea id={field.name} placeholder={field.placeholder} />
          ) : (
            <input
              id={field.name}
              type="text"
              placeholder={field.placeholder}
            />
          )}
        </div>
      ))}
      <button type="submit">{submitLabel}</button>
    </form>
  );
}
