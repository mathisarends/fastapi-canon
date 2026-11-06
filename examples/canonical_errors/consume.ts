import { createClient, ProblemError } from "./generated/typescript/index.js";

async function consume(baseUrl: string): Promise<void> {
  const client = createClient({ baseUrl });
  const observed: number[] = [];
  const problems: string[] = [];
  try {
    const project = await client.projects.get({ projectId: 1 });
    const events: number[] = [];
    for await (const event of client.projects.events({ projectId: 1 })) {
      events.push(event.sequence);
    }
    await client.projects.cancel({ projectId: 1 });
    const stream = async (projectId: number): Promise<void> => {
      observed.length = 0;
      for await (const event of client.projects.events({ projectId })) {
        observed.push(event.sequence);
      }
    };
    const failures: [string, () => Promise<unknown>][] = [
      ["http", () => client.projects.get({ projectId: 0 })],
      ["admission", () => stream(0)],
      ["terminal", () => stream(2)],
      ["rpc", () => client.projects.cancel({ projectId: 0 })],
    ];
    for (const [name, operation] of failures) {
      try {
        await operation();
      } catch (error) {
        if (!(error instanceof ProblemError)) throw error;
        if (
          error.problem.type !== "https://api.example.com/problems/project_missing" ||
          error.problem.code !== "project_missing" ||
          error.problem.replacement !== null
        ) throw error;
        if (name === "admission" && observed.length !== 0) throw error;
        if (name === "terminal" && JSON.stringify(observed) !== "[1]") throw error;
        problems.push(name);
        continue;
      }
      throw new Error(`${name} did not raise the shared problem`);
    }
    console.log(JSON.stringify({ project: project.id, events, rpc: "cancelled", problems }));
  } finally {
    await client.close();
  }
}

consume(process.argv[2] ?? "http://127.0.0.1:8000").catch(error => {
  console.error(error);
  process.exitCode = 1;
});
