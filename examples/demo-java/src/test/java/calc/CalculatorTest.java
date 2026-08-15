package calc;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;

import org.junit.jupiter.api.Test;

class CalculatorTest {

    @Test
    void clampBounds() {
        assertEquals(5, Calculator.clamp(5, 0, 10));
        assertEquals(0, Calculator.clamp(-3, 0, 10));
        assertEquals(10, Calculator.clamp(42, 0, 10));
    }

    @Test
    void clampEdges() {
        assertEquals(0, Calculator.clamp(0, 0, 10));
        assertEquals(10, Calculator.clamp(10, 0, 10));
    }

    @Test
    void average() {
        assertEquals(2.5, Calculator.average(List.of(1.0, 2.0, 3.0, 4.0)), 1e-9);
        assertEquals(0.0, Calculator.average(List.of()), 1e-9);
    }

    @Test
    void isEven() {
        assertTrue(Calculator.isEven(0));
        assertFalse(Calculator.isEven(1));
        assertTrue(Calculator.isEven(10));
    }

    @Test
    void countAbove() {
        assertEquals(2, Calculator.countAbove(List.of(1.0, 2.0, 3.0, 4.0, 5.0), 3.0));
        assertEquals(0, Calculator.countAbove(List.of(), 1.0));
    }

    @Test
    void describe() {
        assertEquals("zero", Calculator.describe(0));
        assertEquals("negative", Calculator.describe(-1));
        assertEquals("positive", Calculator.describe(5));
    }
}
