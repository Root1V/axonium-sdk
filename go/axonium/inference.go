package axonium

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"os"
	"strings"
)

// EmbeddingRequest asks for vector embeddings.
type EmbeddingRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`

	EncodingFormat string `json:"encoding_format,omitempty"`
}

func (r *EmbeddingRequest) validate() error {
	var problems []string
	if strings.TrimSpace(r.Model) == "" {
		problems = append(problems, "model is required")
	}
	if len(r.Input) == 0 {
		problems = append(problems, "input must not be empty")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalidRequest, strings.Join(problems, "; "))
	}
	return nil
}

// Embedding is one vector in an embeddings response.
type Embedding struct {
	Object    string    `json:"object"`
	Index     int       `json:"index"`
	Embedding []float64 `json:"embedding"`
}

// EmbeddingList is an embeddings response.
//
// Usage carries no CompletionTokens here: there is no generation phase, so the field is nil rather
// than zero -- "not applicable", not "measured and free".
type EmbeddingList struct {
	Object string      `json:"object"`
	Model  string      `json:"model"`
	Data   []Embedding `json:"data"`
	Usage  *Usage      `json:"usage,omitempty"`

	Meta ResponseMeta `json:"-"`

	// Raw is the decoded body as received, so backend-specific fields this SDK does
	// not model stay reachable rather than being dropped.
	Raw map[string]any `json:"-"`
}

func (l *EmbeddingList) setRaw(raw map[string]any) { l.Raw = raw }

// EmbeddingsService is the embeddings API.
type EmbeddingsService struct{ client *Client }

// Create returns embeddings for the given inputs.
func (s *EmbeddingsService) Create(ctx context.Context, req EmbeddingRequest) (*EmbeddingList, error) {
	if err := req.validate(); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, req.Model, modalitiesEmbeddings); err != nil {
		return nil, err
	}

	var out EmbeddingList
	meta, err := s.client.doJSON(ctx, http.MethodPost, "/v1/embeddings", req, &out)
	if err != nil {
		return nil, err
	}
	out.Meta = meta
	return &out, nil
}

// ImageRequest asks for generated images.
type ImageRequest struct {
	Model  string `json:"model"`
	Prompt string `json:"prompt"`
	N      int    `json:"n,omitempty"`
	Size   string `json:"size,omitempty"`
}

func (r *ImageRequest) validate() error {
	var problems []string
	if strings.TrimSpace(r.Model) == "" {
		problems = append(problems, "model is required")
	}
	if strings.TrimSpace(r.Prompt) == "" {
		problems = append(problems, "prompt is required")
	}
	if r.N < 0 {
		problems = append(problems, "n must not be negative")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalidRequest, strings.Join(problems, "; "))
	}
	return nil
}

// Image is one generated image, returned as base64 rather than a URL.
type Image struct {
	B64JSON       string `json:"b64_json"`
	RevisedPrompt string `json:"revised_prompt,omitempty"`
}

// Bytes decodes the image.
func (i *Image) Bytes() ([]byte, error) {
	decoded, err := base64.StdEncoding.DecodeString(i.B64JSON)
	if err != nil {
		return nil, fmt.Errorf("%w: the image payload was not valid base64: %v", ErrTransport, err)
	}
	return decoded, nil
}

// Save writes the decoded image to path.
func (i *Image) Save(path string) error {
	data, err := i.Bytes()
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

// ImageList is an image generation response.
type ImageList struct {
	Created      int64   `json:"created"`
	Model        string  `json:"model"`
	OutputFormat string  `json:"output_format,omitempty"`
	Data         []Image `json:"data"`
	Usage        *Usage  `json:"usage,omitempty"`

	Meta ResponseMeta `json:"-"`

	// Raw is the decoded body as received, so backend-specific fields this SDK does
	// not model stay reachable rather than being dropped.
	Raw map[string]any `json:"-"`
}

func (l *ImageList) setRaw(raw map[string]any) { l.Raw = raw }

// ImagesService is the image generation API.
type ImagesService struct{ client *Client }

// Generate creates images from a prompt. Generation legitimately takes minutes, so the default
// request timeout is long; pass a context deadline where a faster failure is preferable.
func (s *ImagesService) Generate(ctx context.Context, req ImageRequest) (*ImageList, error) {
	if err := req.validate(); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, req.Model, modalitiesImages); err != nil {
		return nil, err
	}

	var out ImageList
	meta, err := s.client.doJSON(ctx, http.MethodPost, "/v1/images/generations", req, &out)
	if err != nil {
		return nil, err
	}
	out.Meta = meta
	return &out, nil
}
