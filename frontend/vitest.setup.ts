import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

if (typeof document !== "undefined") {
  const { cleanup } = await import("@testing-library/react");
  afterEach(cleanup);
}

process.env.TZ = "UTC";

const [major] = process.versions.node.split(".").map(Number);
if (major < 22) {
  throw new Error(
    `Node ${process.versions.node} is too old — this project needs Node 22+ ` +
      `(see .nvmrc). jsdom tests fail in confusing ways below that. Run \`nvm use\`.`,
  );
}
