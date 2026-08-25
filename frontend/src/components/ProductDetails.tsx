import type { ProductReviewHistory } from "../features/reviews/types";
import type { ArticleState } from "../features/sellers/types";

interface ProductDetailsProps {
  product: ProductReviewHistory;
  stateLabel: Record<ArticleState, string>;
}

/**
 * Everything about the product that the row itself has to truncate: the full
 * title, the ids and the identifiers that let a person tell two look-alike
 * goods apart.
 */
export function ProductDetails({ product, stateLabel }: ProductDetailsProps) {
  const rows: Array<{ label: string; value: React.ReactNode }> = [
    {
      label: "Артикул WB",
      value: (
        <a
          className="wb-article-link"
          href={`https://www.wildberries.ru/catalog/${product.article}/detail.aspx`}
          target="_blank"
          rel="noreferrer"
        >
          {product.article}
        </a>
      ),
    },
    { label: "Артикул продавца", value: product.vendor_code || "—" },
    { label: "Карточка (склейка)", value: product.imt_id === null ? "—" : String(product.imt_id) },
    { label: "Предмет", value: product.subject_name || "—" },
    { label: "Бренд", value: product.brand || "—" },
    { label: "Состояние", value: stateLabel[product.state] },
  ];

  return (
    <div className="product-details">
      <h3 className="product-details-name">{product.name}</h3>
      <dl className="product-details-grid">
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
