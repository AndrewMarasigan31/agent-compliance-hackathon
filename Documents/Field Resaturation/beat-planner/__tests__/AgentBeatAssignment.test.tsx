/**
 * @jest-environment jsdom
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import AgentBeatAssignment from "@/components/AgentBeatAssignment";
import { BeatResult } from "@/types";

const makeBeats = (): BeatResult[] => [
  {
    beatId: 1,
    gcu: "GCU-Alpha",
    color: "#ff0000",
    storeCount: 5,
    stores: [],
  },
  {
    beatId: 2,
    gcu: "GCU-Alpha",
    color: "#00ff00",
    storeCount: 3,
    stores: [],
  },
  {
    beatId: 3,
    gcu: "GCU-Beta",
    color: "#0000ff",
    storeCount: 7,
    stores: [],
  },
];

const agents = ["Alice", "Bob"];

describe("AgentBeatAssignment", () => {
  it("renders beats grouped under correct GCU header labels", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    expect(screen.getByText("GCU-Alpha")).toBeInTheDocument();
    expect(screen.getByText("GCU-Beta")).toBeInTheDocument();
    expect(screen.getByText("Beat 1")).toBeInTheDocument();
    expect(screen.getByText("Beat 2")).toBeInTheDocument();
    expect(screen.getByText("Beat 3")).toBeInTheDocument();
  });

  it("dropdown options match the agents prop exactly", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(3);

    for (const select of selects) {
      const options = Array.from(select.querySelectorAll("option")).map(
        (o) => o.value
      );
      expect(options).toContain("Alice");
      expect(options).toContain("Bob");
      expect(options).toContain("");
    }
  });

  it("progress indicator starts at '0 of N beats assigned'", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    expect(screen.getByText("0 of 3 beats assigned")).toBeInTheDocument();
  });

  it("progress indicator updates as dropdowns are changed", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Alice" } });
    expect(screen.getByText("1 of 3 beats assigned")).toBeInTheDocument();

    fireEvent.change(selects[1], { target: { value: "Bob" } });
    expect(screen.getByText("2 of 3 beats assigned")).toBeInTheDocument();
  });

  it("Continue button is disabled when any beat is unassigned", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).toBeDisabled();
  });

  it("Continue button is enabled when all beats have an agent selected", () => {
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={jest.fn()}
        onContinue={jest.fn()}
      />
    );

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Alice" } });
    fireEvent.change(selects[1], { target: { value: "Bob" } });
    fireEvent.change(selects[2], { target: { value: "Alice" } });

    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).not.toBeDisabled();
    expect(screen.getByText("3 of 3 beats assigned")).toBeInTheDocument();
  });

  it("onAssignmentChange is called with correct (beatId, agentName) arguments", () => {
    const onAssignmentChange = jest.fn();
    render(
      <AgentBeatAssignment
        beats={makeBeats()}
        agents={agents}
        onAssignmentChange={onAssignmentChange}
        onContinue={jest.fn()}
      />
    );

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Bob" } });

    expect(onAssignmentChange).toHaveBeenCalledWith(1, "Bob");
  });
});
