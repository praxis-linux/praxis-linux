import { BASIC_FUNCTIONS, BasicCatalogThemeSchema, ButtonApi, Catalog } from "@a2ui/web_core/v0_9";
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

/**
 * Everything the agent may put on the Canvas. A subset of the A2UI basic
 * catalog: components that load remote URLs (Image, Video, AudioPlayer, Icon)
 * and the `openUrl` function are left out, so a surface cannot make network
 * requests or navigate away.
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
  ],
  BASIC_FUNCTIONS.filter((fn) => fn.name !== "openUrl"),
  BasicCatalogThemeSchema,
);
