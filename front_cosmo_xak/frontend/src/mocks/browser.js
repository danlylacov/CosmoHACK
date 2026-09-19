import { setupWorker } from "msw/browser";
import { handlers } from "../api/mocks/handlers.js";

export const worker = setupWorker(...handlers);
