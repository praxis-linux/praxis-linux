import { BASIC_FUNCTIONS, BasicCatalogThemeSchema, ButtonApi, Catalog } from "@a2ui/web_core/v0_9";
import { z } from "zod";
import {
  Card,
  CheckBox,
  ChoicePicker,
  Column,
  createComponentImplementation,
  DateTimeInput,
  Divider,
  List,
  Modal,
  Row,
  Slider,
  Tabs,
  Text,
  TextField,
  type ReactComponentImplementation,
} from "@a2ui/react/v0_9";

// Must match PRAXIS_CATALOG_ID in agent/src/praxis_agent/ui/a2ui.py.
export const PRAXIS_CATALOG_ID = "praxis-linux:catalog:phase0";

// Same API as the basic catalog's Button, with class names the Canvas can style
// per variant.
const Button = createComponentImplementation(ButtonApi, ({ props, buildChild }) => (
  <button
    className={`praxis-button ${props.variant ?? "default"}`}
    onClick={props.action}
    disabled={props.isValid === false}
  >
    {props.child ? buildChild(props.child) : null}
  </button>
));

// A file's image, carried inline. The basic catalog's Image takes any URL and is
// left out; this takes only the data the agent read from the file, so showing an
// image never fetches anything. SVG is not accepted: it can carry script.
const FileImageApi = {
  name: "FileImage",
  schema: z.object({
    src: z
      .string()
      .max(12_000_000)
      .regex(/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$/),
    description: z.string().max(500).optional(),
  }),
};

const FileImage = createComponentImplementation(FileImageApi, ({ props }) => (
  <img className="file-image" src={props.src} alt={props.description ?? ""} />
));

/**
 * Everything the agent may put on the Canvas. A subset of the A2UI basic
 * catalog: components that load remote URLs (Image, Video, AudioPlayer, Icon)
 * and the `openUrl` function are left out, so a surface cannot make network
 * requests or navigate away. FileImage shows images from inline data only.
 */
export const praxisCatalog = new Catalog<ReactComponentImplementation>(
  PRAXIS_CATALOG_ID,
  "0.9",
  [
    Text,
    Row,
    Column,
    List,
    Card,
    Tabs,
    Divider,
    Modal,
    Button,
    TextField,
    CheckBox,
    ChoicePicker,
    Slider,
    DateTimeInput,
    FileImage,
  ],
  BASIC_FUNCTIONS.filter((fn) => fn.name !== "openUrl"),
  BasicCatalogThemeSchema,
);
