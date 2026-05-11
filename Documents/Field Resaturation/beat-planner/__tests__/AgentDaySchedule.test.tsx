/**
 * @jest-environment jsdom
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import AgentDaySchedule from "@/components/AgentDaySchedule";
import { BeatResult } from "@/types";

const agents = ["Alice", "Bob"];

const beats: BeatResult[] = [
  {
    beatId: 1,
    gcu: "GCU-Alpha",
    color: "#ff0000",
    storeCount: 5,
    stores: [],
    assignedAgent: "Alice",
  },
  {
    beatId: 2,
    gcu: "GCU-Beta",
    color: "#00ff00",
    storeCount: 3,
    stores: [],
    assignedAgent: "Bob",
  },
  {
    beatId: 3,
    gcu: "GCU-Alpha",
    color: "#0000ff",
    storeCount: 7,
    stores: [],
    assignedAgent: "Alice",
  },
];

const dayAssignments: Record<string, Record<number, string>> = {
  Alice: {},
  Bob: {},
};

describe("AgentDaySchedule", () => {
  it("renders one tab per agent", () => {
    render(
      <AgentDaySchedule
        agents={agents}
        beats={beats}
        dayAssignments={dayAssignments}
        onDayChange={jest.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "Alice" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bob" })).toBeInTheDocument();
  });

  it("active tab shows only beats assigned to that agent", () => {
    render(
      <AgentDaySchedule
        agents={agents}
        beats={beats}
        dayAssignments={dayAssignments}
        onDayChange={jest.fn()}
      />
    );

    // Default active agent is Alice (first in array)
    expect(screen.getByText("Beat 1")).toBeInTheDocument();
    expect(screen.getByText("Beat 3")).toBeInTheDocument();
    expect(screen.queryByText("Beat 2")).not.toBeInTheDocument();
  });

  it("switching tabs updates the visible beat list", () => {
    render(
      <AgentDaySchedule
        agents={agents}
        beats={beats}
        dayAssignments={dayAssignments}
        onDayChange={jest.fn()}
      />
    );

    // Switch to Bob's tab
    fireEvent.click(screen.getByRole("button", { name: "Bob" }));

    expect(screen.getByText("Beat 2")).toBeInTheDocument();
    expect(screen.queryByText("Beat 1")).not.toBeInTheDocument();
    expect(screen.queryByText("Beat 3")).not.toBeInTheDocument();
  });
});
