/**
 * @jest-environment jsdom
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import UploadForm from "@/components/UploadForm";

describe("UploadForm", () => {
  it("'+ Add Agent' button appends a new input field", () => {
    render(<UploadForm onSuccess={jest.fn()} />);

    const inputs = screen.getAllByPlaceholderText(/agent \d+ name/i);
    expect(inputs).toHaveLength(1);

    fireEvent.click(screen.getByText("+ Add Agent"));

    const inputsAfter = screen.getAllByPlaceholderText(/agent \d+ name/i);
    expect(inputsAfter).toHaveLength(2);
  });

  it("upload button is disabled when any name field is empty", () => {
    render(<UploadForm onSuccess={jest.fn()} />);

    const uploadButton = screen.getByRole("button", { name: /upload/i });
    expect(uploadButton).toBeDisabled();

    // Add second agent and fill first, leave second empty
    fireEvent.click(screen.getByText("+ Add Agent"));
    const inputs = screen.getAllByPlaceholderText(/agent \d+ name/i);
    fireEvent.change(inputs[0], { target: { value: "Alice" } });

    expect(uploadButton).toBeDisabled();
  });

  it("upload button is enabled when all name fields are filled and a file is selected", () => {
    render(<UploadForm onSuccess={jest.fn()} />);

    const uploadButton = screen.getByRole("button", { name: /upload/i });

    // Fill in agent name
    const input = screen.getByPlaceholderText(/agent 1 name/i);
    fireEvent.change(input, { target: { value: "Alice" } });

    // Still disabled — no file yet
    expect(uploadButton).toBeDisabled();

    // Select a file
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["store,lat,lng"], "stores.csv", { type: "text/csv" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(uploadButton).not.toBeDisabled();
  });
});
